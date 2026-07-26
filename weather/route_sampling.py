from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

from weather.risk import risk_level, score_metrics_for_mode


ROUTE_WEATHER_SCORING_VERSION = "open-meteo-route-weather-v2"


def parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip().replace("Z", "+00:00")
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def valid_coordinate(latitude: Any, longitude: Any) -> bool:
    try:
        lat = float(latitude)
        lng = float(longitude)
    except (TypeError, ValueError):
        return False
    return -90 <= lat <= 90 and -180 <= lng <= 180 and not (lat == 0 and lng == 0)


def coordinate(latitude: Any, longitude: Any) -> tuple[float, float] | None:
    if not valid_coordinate(latitude, longitude):
        return None
    return float(latitude), float(longitude)


def parse_linestring(value: Any) -> list[tuple[float, float]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if isinstance(value, dict):
        if str(value.get("type") or "").casefold() != "linestring":
            return []
        value = value.get("coordinates")
    if not isinstance(value, list):
        return []
    points: list[tuple[float, float]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            return []
        longitude, latitude = item[0], item[1]
        point = coordinate(latitude, longitude)
        if point is None:
            return []
        points.append(point)
    return points if len(points) >= 2 else []


def haversine_km(left: tuple[float, float], right: tuple[float, float]) -> float:
    left_lat, left_lng = map(math.radians, left)
    right_lat, right_lng = map(math.radians, right)
    latitude_delta = right_lat - left_lat
    longitude_delta = right_lng - left_lng
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(left_lat) * math.cos(right_lat) * math.sin(longitude_delta / 2) ** 2
    )
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(value)))


def interpolate_point(
    left: tuple[float, float], right: tuple[float, float], fraction: float
) -> tuple[float, float]:
    latitude = left[0] + (right[0] - left[0]) * fraction
    longitude_delta = ((right[1] - left[1] + 180) % 360) - 180
    longitude = ((left[1] + longitude_delta * fraction + 180) % 360) - 180
    return latitude, longitude


def point_along_line(points: list[tuple[float, float]], fraction: float) -> tuple[float, float]:
    if fraction <= 0:
        return points[0]
    if fraction >= 1:
        return points[-1]
    lengths = [haversine_km(left, right) for left, right in zip(points, points[1:])]
    total = sum(lengths)
    if total <= 0:
        return points[0]
    target = total * fraction
    traversed = 0.0
    for index, length in enumerate(lengths):
        if traversed + length >= target:
            local_fraction = (target - traversed) / length if length else 0.0
            return interpolate_point(points[index], points[index + 1], local_fraction)
        traversed += length
    return points[-1]


def build_route_samples(segment: dict[str, Any], sample_count: int = 5) -> dict[str, Any]:
    geometry = parse_linestring(segment.get("geometry") or segment.get("geometry_json"))
    segment_id = str(segment.get("segment_id") or segment.get("element_id") or "unknown")
    if geometry:
        count = max(2, sample_count)
        fractions = [index / (count - 1) for index in range(count)]
        points = [point_along_line(geometry, fraction) for fraction in fractions]
        inferred_geometry = bool(segment.get("is_inferred")) or str(segment.get("source_type") or "").casefold() == "estimated_by_graph"
        method = "estimated_geometry_linestring" if inferred_geometry else "geometry_linestring"
        confidence = 0.55 if inferred_geometry else 1.0
        geometry_status = "estimated" if inferred_geometry else "available"
    else:
        origin = coordinate(segment.get("from_lat"), segment.get("from_lng"))
        destination = coordinate(segment.get("to_lat"), segment.get("to_lng"))
        if origin is None or destination is None:
            return {
                "segment_id": segment_id,
                "samples": [],
                "sampling_method": "unavailable",
                "sampling_confidence": 0.0,
                "geometry_status": "unavailable",
            }
        points = [origin] if origin == destination else [origin, destination]
        fractions = [0.0] if len(points) == 1 else [0.0, 1.0]
        method = "endpoint_fallback"
        confidence = 0.35
        geometry_status = "unavailable"
    samples = [
        {
            "sample_id": f"{segment_id}:weather:{index}",
            "index": index,
            "fraction": round(fraction, 6),
            "latitude": round(point[0], 6),
            "longitude": round(point[1], 6),
        }
        for index, (fraction, point) in enumerate(zip(fractions, points))
    ]
    return {
        "segment_id": segment_id,
        "samples": samples,
        "sampling_method": method,
        "sampling_confidence": confidence,
        "geometry_status": geometry_status,
    }


def hourly_metrics_at(
    weather_payload: dict[str, Any],
    marine_payload: dict[str, Any],
    target_time: datetime,
) -> tuple[dict[str, Any] | None, str | None]:
    hourly = weather_payload.get("hourly") or {}
    valid_times = [
        (index, parsed)
        for index, value in enumerate(hourly.get("time") or [])
        if (parsed := parse_utc(value)) is not None
    ]
    if not valid_times or target_time < valid_times[0][1] or target_time > valid_times[-1][1]:
        return None, None
    index, selected_time = min(
        valid_times,
        key=lambda item: abs((item[1] - target_time).total_seconds()),
    )
    metrics = {
        key: values[index] if isinstance(values, list) and index < len(values) else None
        for key, values in hourly.items()
        if key != "time"
    }
    marine_hourly = marine_payload.get("hourly") or {}
    marine_time_indices = {
        parsed: marine_index
        for marine_index, value in enumerate(marine_hourly.get("time") or [])
        if (parsed := parse_utc(value)) is not None
    }
    marine_index = marine_time_indices.get(selected_time)
    if marine_index is not None:
        for key, values in marine_hourly.items():
            if key != "time" and isinstance(values, list) and marine_index < len(values):
                metrics[key] = values[marine_index]
    return metrics, selected_time.isoformat()


def score_sample(
    sample: dict[str, Any],
    *,
    mode: str,
    duration_hours: float | None,
    reference_time: datetime,
    weather_payload: dict[str, Any],
    marine_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    eta_available = duration_hours is not None and duration_hours >= 0
    target_time = reference_time
    if eta_available:
        target_time = reference_time + timedelta(hours=duration_hours * float(sample["fraction"]))
    metrics, selected_time = hourly_metrics_at(
        weather_payload,
        marine_payload or {},
        target_time,
    )
    eta_status = "available" if eta_available else "unavailable_current_time_fallback"
    if metrics is None:
        return {
            **sample,
            "target_time": target_time.isoformat(),
            "selected_forecast_time": None,
            "eta_status": eta_status,
            "status": "unavailable_outside_forecast_horizon",
            "score": None,
            "confidence": 0.0,
            "data_completeness": 0.0,
            "factors": [],
        }
    result = score_metrics_for_mode(metrics, mode)
    return {
        **sample,
        "target_time": target_time.isoformat(),
        "selected_forecast_time": selected_time,
        "eta_status": eta_status,
        "status": "available" if result["score"] is not None else "unavailable",
        "score": result["score"],
        "level": result["level"],
        "confidence": round(result["confidence"] * (1.0 if eta_available else 0.7), 4),
        "data_completeness": result["data_completeness"],
        "factors": result["factors"],
        "metrics": metrics,
    }


def aggregate_route_weather(
    sample_results: list[dict[str, Any]],
    *,
    sampling_method: str,
    sampling_confidence: float,
) -> dict[str, Any]:
    valid = [sample for sample in sample_results if sample.get("score") is not None]
    if not sample_results or not valid:
        return {
            "score": None,
            "level": "UNKNOWN",
            "status": "unavailable",
            "confidence": 0.0,
            "data_completeness": 0.0,
            "sampling_method": sampling_method,
            "sample_count": len(sample_results),
            "valid_sample_count": 0,
            "factors": [],
        }
    scores = [float(sample["score"]) for sample in valid]
    score = 0.6 * max(scores) + 0.4 * mean(scores)
    sample_coverage = len(valid) / len(sample_results)
    completeness = sample_coverage * mean(float(sample["data_completeness"]) for sample in valid)
    confidence = sampling_confidence * sample_coverage * mean(float(sample["confidence"]) for sample in valid)
    factor_scores: dict[str, list[float]] = {}
    for sample in valid:
        for factor in sample.get("factors") or []:
            factor_scores.setdefault(str(factor["factor"]), []).append(float(factor["risk_score"]))
    factors = [
        {
            "factor": factor,
            "risk_score": round(max(values), 1),
            "sample_average": round(mean(values), 1),
        }
        for factor, values in factor_scores.items()
    ]
    factors.sort(key=lambda item: item["risk_score"], reverse=True)
    status = "available" if sampling_method == "geometry_linestring" and completeness >= 0.8 else "partial"
    return {
        "score": round(score, 1),
        "level": risk_level(score),
        "status": status,
        "confidence": round(confidence, 4),
        "data_completeness": round(completeness, 4),
        "sampling_method": sampling_method,
        "sample_count": len(sample_results),
        "valid_sample_count": len(valid),
        "factors": factors,
        "maximum_sample_risk": round(max(scores), 1),
        "average_sample_risk": round(mean(scores), 1),
    }
