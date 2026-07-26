from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from app.vehicle_network.models import RouteGenerateRequest
from app.vehicle_network.services import RouteGenerationService
from weather.client import OpenMeteoClient
from weather.config import WeatherSettings
from weather.repository import ensure_schema, list_route_segments, write_route_weather
from weather.route_sampling import (
    ROUTE_WEATHER_SCORING_VERSION,
    aggregate_route_weather,
    build_route_samples,
    score_sample,
)


ROUTE_UPDATE_LOCK = threading.Lock()
FEASIBILITY_SERVICE = RouteGenerationService()


def point_key(point: dict[str, Any]) -> str:
    return f"{float(point['latitude']):.5f},{float(point['longitude']):.5f}"


def optional_duration(value: Any) -> float | None:
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return duration if duration >= 0 else None


def route_eligibility(segment: dict[str, Any]) -> tuple[bool, str]:
    if str(segment.get("data_status") or "").casefold() == "synthetic":
        return False, "synthetic_route"
    if (
        str(segment.get("source_type") or "").casefold() == "estimated_by_graph"
        and int(segment.get("active_route_count") or 0) == 0
    ):
        return False, "inactive_estimated_route"
    required = ("from_lat", "from_lng", "to_lat", "to_lng")
    if any(segment.get(field) is None for field in required):
        return False, "missing_coordinates"
    origin = {
        "latitude": segment["from_lat"],
        "longitude": segment["from_lng"],
        "country": segment.get("from_country"),
        "country_code": segment.get("from_country_code"),
        "labels": segment.get("from_labels") or [],
    }
    destination = {
        "latitude": segment["to_lat"],
        "longitude": segment["to_lng"],
        "country": segment.get("to_country"),
        "country_code": segment.get("to_country_code"),
        "labels": segment.get("to_labels") or [],
    }
    mode = str(segment.get("mode") or "").casefold()
    request = RouteGenerateRequest(
        origin="origin",
        destination="destination",
        mode_preferences=[mode],
    )
    feasible, _ = FEASIBILITY_SERVICE._mode_candidates(origin, destination, request)
    return (True, "eligible") if mode in feasible else (False, "geographically_infeasible")


def batches(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def fetch_payloads(
    client: OpenMeteoClient,
    points: dict[str, dict[str, Any]],
    *,
    forecast_hours: int,
    batch_size: int,
    marine: bool,
    errors: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], int]:
    payloads: dict[str, dict[str, Any]] = {}
    request_count = 0
    entries = list(points.items())
    for batch in batches(entries, batch_size):
        keys = [key for key, _ in batch]
        coordinates = [point for _, point in batch]
        try:
            response = (
                client.marine_batch(coordinates, forecast_hours)
                if marine
                else client.weather_batch(coordinates, forecast_hours)
            )
            request_count += 1
            for key, payload in zip(keys, response):
                if isinstance(payload, dict):
                    payloads[key] = payload
            if len(response) != len(keys):
                errors.append(
                    {
                        "stage": "marine_response_count" if marine else "weather_response_count",
                        "requested": len(keys),
                        "received": len(response),
                    }
                )
        except Exception as exc:
            errors.append(
                {
                    "stage": "marine_request" if marine else "weather_request",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "point_count": len(keys),
                }
            )
    return payloads, request_count


def build_snapshot_row(
    segment: dict[str, Any],
    plan: dict[str, Any],
    sample_results: list[dict[str, Any]],
    aggregate: dict[str, Any],
    *,
    fetched_at: datetime,
    expires_at: datetime,
    forecast_hours: int,
    marine_available: bool,
) -> dict[str, Any]:
    segment_id = str(segment["segment_id"])
    period = fetched_at.replace(minute=0, second=0, microsecond=0).isoformat()
    snapshot_hash = hashlib.sha256(
        f"{segment_id}|{period}|{ROUTE_WEATHER_SCORING_VERSION}".encode("utf-8")
    ).hexdigest()[:24]
    forecast_times = sorted(
        str(sample["selected_forecast_time"])
        for sample in sample_results
        if sample.get("selected_forecast_time")
    )
    source = "Open-Meteo Forecast API"
    if marine_available:
        source += " + Open-Meteo Marine Weather API"
    return {
        "element_id": segment["element_id"],
        "segment_id": segment_id,
        "mode": segment["mode"],
        "snapshot_id": f"route-weather-{snapshot_hash}",
        "fetched_at": fetched_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "forecast_start_at": forecast_times[0] if forecast_times else None,
        "forecast_end_at": forecast_times[-1] if forecast_times else None,
        "forecast_hours": forecast_hours,
        "score": aggregate["score"],
        "level": aggregate["level"],
        "status": aggregate["status"],
        "confidence": aggregate["confidence"],
        "data_completeness": aggregate["data_completeness"],
        "sampling_method": aggregate["sampling_method"],
        "geometry_status": plan["geometry_status"],
        "sample_count": aggregate["sample_count"],
        "valid_sample_count": aggregate["valid_sample_count"],
        "maximum_sample_risk": aggregate.get("maximum_sample_risk"),
        "average_sample_risk": aggregate.get("average_sample_risk"),
        "samples_json": json.dumps(sample_results, ensure_ascii=False, separators=(",", ":")),
        "factors_json": json.dumps(aggregate["factors"], ensure_ascii=False, separators=(",", ":")),
        "source": source,
        "marine_status": "available" if marine_available else "unavailable",
        "scoring_version": ROUTE_WEATHER_SCORING_VERSION,
    }


def update_route_weather(
    segment_ids: list[str] | None = None,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    client: OpenMeteoClient | None = None,
) -> dict[str, Any]:
    if not ROUTE_UPDATE_LOCK.acquire(blocking=False):
        raise RuntimeError("Route weather update is already running")
    settings = WeatherSettings()
    own_client = client is None
    weather_client = client or OpenMeteoClient(settings)
    started = datetime.now(timezone.utc)
    errors: list[dict[str, Any]] = []
    try:
        segments = list_route_segments(segment_ids)
        if limit is not None:
            segments = segments[: max(0, limit)]
        plans: list[tuple[dict[str, Any], dict[str, Any]]] = []
        unique_points: dict[str, dict[str, Any]] = {}
        sea_points: dict[str, dict[str, Any]] = {}
        skip_reasons: dict[str, int] = {}
        for segment in segments:
            eligible, reason = route_eligibility(segment)
            if not eligible:
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                continue
            plan = build_route_samples(segment, settings.route_sample_points)
            if not plan["samples"]:
                skip_reasons["missing_coordinates"] = skip_reasons.get("missing_coordinates", 0) + 1
                continue
            plans.append((segment, plan))
            for sample in plan["samples"]:
                key = point_key(sample)
                unique_points[key] = {
                    "latitude": sample["latitude"],
                    "longitude": sample["longitude"],
                }
                if segment["mode"] == "sea":
                    sea_points[key] = unique_points[key]
        forecast_hours = min(max(settings.route_forecast_hours, 1), 384)
        weather_payloads, weather_requests = fetch_payloads(
            weather_client,
            unique_points,
            forecast_hours=forecast_hours,
            batch_size=settings.batch_size,
            marine=False,
            errors=errors,
        )
        marine_payloads, marine_requests = fetch_payloads(
            weather_client,
            sea_points,
            forecast_hours=forecast_hours,
            batch_size=settings.batch_size,
            marine=True,
            errors=errors,
        )
        fetched_at = datetime.now(timezone.utc)
        expires_at = fetched_at + timedelta(hours=settings.route_risk_ttl_hours)
        rows: list[dict[str, Any]] = []
        unavailable_routes = 0
        route_summaries: list[dict[str, Any]] = []
        for segment, plan in plans:
            sample_results: list[dict[str, Any]] = []
            marine_available = False
            for sample in plan["samples"]:
                key = point_key(sample)
                payload = weather_payloads.get(key)
                if payload is None:
                    sample_results.append(
                        {
                            **sample,
                            "status": "unavailable_provider_request_failed",
                            "score": None,
                            "confidence": 0.0,
                            "data_completeness": 0.0,
                            "factors": [],
                        }
                    )
                    continue
                marine_available = marine_available or (
                    segment["mode"] == "sea" and key in marine_payloads
                )
                sample_results.append(
                    score_sample(
                        sample,
                        mode=segment["mode"],
                        duration_hours=optional_duration(segment.get("duration_hours")),
                        reference_time=started,
                        weather_payload=payload,
                        marine_payload=marine_payloads.get(key),
                    )
                )
            aggregate = aggregate_route_weather(
                sample_results,
                sampling_method=plan["sampling_method"],
                sampling_confidence=plan["sampling_confidence"],
            )
            if aggregate["score"] is None:
                unavailable_routes += 1
            else:
                rows.append(
                    build_snapshot_row(
                        segment,
                        plan,
                        sample_results,
                        aggregate,
                        fetched_at=fetched_at,
                        expires_at=expires_at,
                        forecast_hours=forecast_hours,
                        marine_available=marine_available,
                    )
                )
            if len(route_summaries) < 50:
                route_summaries.append(
                    {
                        "segmentId": segment["segment_id"],
                        "mode": segment["mode"],
                        "score": aggregate["score"],
                        "status": aggregate["status"],
                        "confidence": aggregate["confidence"],
                        "dataCompleteness": aggregate["data_completeness"],
                        "samplingMethod": aggregate["sampling_method"],
                        "validSamples": aggregate["valid_sample_count"],
                        "sampleCount": aggregate["sample_count"],
                    }
                )
        if not dry_run:
            ensure_schema()
        written = write_route_weather(rows, dry_run=dry_run)
        finished = datetime.now(timezone.utc)
        return {
            "startedAt": started.isoformat(),
            "finishedAt": finished.isoformat(),
            "durationMs": round((finished - started).total_seconds() * 1000),
            "dryRun": dry_run,
            "segmentsScanned": len(segments),
            "segmentsSampled": len(plans),
            "segmentsWritten": written,
            "segmentsPlannedForWrite": len(rows),
            "segmentsUnavailable": unavailable_routes,
            "segmentsSkipped": sum(skip_reasons.values()),
            "skipReasons": skip_reasons,
            "uniqueWeatherPoints": len(unique_points),
            "uniqueMarinePoints": len(sea_points),
            "weatherRequests": weather_requests,
            "marineRequests": marine_requests,
            "forecastHours": forecast_hours,
            "scoringVersion": ROUTE_WEATHER_SCORING_VERSION,
            "errors": errors,
            "routeSample": route_summaries,
        }
    finally:
        if own_client:
            weather_client.close()
        ROUTE_UPDATE_LOCK.release()
