"""Shortest-path helpers for scored supply-chain route segments."""

from __future__ import annotations

import heapq
import json
from collections import defaultdict
from typing import Any

from app.provider_risk import FACTOR_LABELS


UNKNOWN_RISK_PENALTY = 1.0
INCOMPLETENESS_PENALTY = 0.25
UNKNOWN_COST_PENALTY = 1_000_000_000.0
UNKNOWN_DURATION_PENALTY = 1_000_000_000.0


def optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def risk_optimization_value(segment: dict[str, Any]) -> float:
    risk_score = optional_float(segment.get("risk_score"))
    if risk_score is None:
        return UNKNOWN_RISK_PENALTY
    completeness = optional_float(segment.get("risk_data_completeness"))
    completeness = min(max(completeness if completeness is not None else 0.0, 0.0), 1.0)
    return round(min(1.0, max(risk_score, 0.0) + (1.0 - completeness) * INCOMPLETENESS_PENALTY), 6)


def segment_weight(segment: dict[str, Any], objective: str, risk_weight: float) -> float:
    risk_score = risk_optimization_value(segment)
    cost_score = float(segment.get("cost_score") if segment.get("cost_score") is not None else 1.0)
    if objective == "min_risk":
        return risk_score
    if objective == "min_cost":
        cost = optional_float(segment.get("cost_usd"))
        return max(cost, 0.000001) if cost is not None else UNKNOWN_COST_PENALTY
    if objective == "fastest":
        duration = optional_float(segment.get("time_days"))
        return max(duration, 0.000001) if duration is not None else UNKNOWN_DURATION_PENALTY
    return risk_weight * risk_score + (1.0 - risk_weight) * cost_score


def shortest_path(
    segments: list[dict[str, Any]],
    origin: str,
    destination: str,
    objective: str,
    risk_weight: float,
) -> dict[str, Any] | None:
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        adjacency[str(segment["from_id"])].append(segment)
    for outgoing in adjacency.values():
        outgoing.sort(key=lambda item: (str(item.get("segment_id")), str(item.get("to_id"))))

    distances = {origin: 0.0}
    previous: dict[str, tuple[str, dict[str, Any]]] = {}
    queue: list[tuple[float, str]] = [(0.0, origin)]

    while queue:
        current_distance, node_id = heapq.heappop(queue)
        if current_distance != distances.get(node_id):
            continue
        if node_id == destination:
            break
        for segment in adjacency.get(node_id, []):
            next_id = str(segment["to_id"])
            candidate = current_distance + segment_weight(segment, objective, risk_weight)
            if candidate < distances.get(next_id, float("inf")):
                distances[next_id] = candidate
                previous[next_id] = (node_id, segment)
                heapq.heappush(queue, (candidate, next_id))

    if destination not in distances:
        return None

    path_segments: list[dict[str, Any]] = []
    cursor = destination
    while cursor != origin:
        parent, segment = previous[cursor]
        path_segments.append(segment)
        cursor = parent
    path_segments.reverse()

    total_cost = sum(float(segment.get("cost_usd") or 0.0) for segment in path_segments)
    total_time = sum(float(segment.get("time_days") or 0.0) for segment in path_segments)
    risk_scores = [value for segment in path_segments if (value := optional_float(segment.get("risk_score"))) is not None]
    completeness_values = [float(segment.get("risk_data_completeness") or 0.0) for segment in path_segments]
    missing_factors = sorted(
        {
            str(factor)
            for segment in path_segments
            for factor in segment.get("risk_missing_factors") or []
        }
    )
    return {
        "objective": objective,
        "optimization_score": round(distances[destination], 6),
        "total_cost_usd": round(total_cost, 2),
        "total_time_days": round(total_time, 2),
        "average_risk_score": round(sum(risk_scores) / len(risk_scores), 4) if risk_scores else None,
        "maximum_risk_score": round(max(risk_scores), 4) if risk_scores else None,
        "risk_status": "unavailable" if not risk_scores else "available" if len(risk_scores) == len(path_segments) and all(segment.get("risk_status") == "available" for segment in path_segments) else "partial",
        "risk_data_completeness": round(sum(completeness_values) / len(completeness_values), 4) if completeness_values else 0.0,
        "risk_known_segments": len(risk_scores),
        "risk_missing_factors": missing_factors,
        "segment_count": len(path_segments),
        "segments": path_segments,
    }


def k_shortest_paths(
    segments: list[dict[str, Any]],
    origins: set[str],
    destinations: set[str],
    objective: str,
    risk_weight: float,
    limit: int,
    max_hops: int = 12,
) -> list[list[dict[str, Any]]]:
    adjacency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        adjacency[str(segment["from_id"])].append(segment)
    for outgoing in adjacency.values():
        outgoing.sort(key=lambda item: (str(item.get("segment_id")), str(item.get("to_id"))))

    queue: list[tuple[float, int, str, tuple[str, ...], tuple[dict[str, Any], ...]]] = []
    sequence = 0
    for origin in sorted(origins):
        heapq.heappush(queue, (0.0, sequence, origin, (origin,), ()))
        sequence += 1

    paths: list[list[dict[str, Any]]] = []
    signatures: set[tuple[str, ...]] = set()
    expansions = 0
    while queue and len(paths) < limit and expansions < 5000:
        cost, _, node_id, visited, path = heapq.heappop(queue)
        if node_id in destinations and path:
            signature = tuple(str(segment["segment_id"]) for segment in path)
            if signature not in signatures:
                signatures.add(signature)
                paths.append(list(path))
            continue
        if len(path) >= max_hops:
            continue
        for segment in adjacency.get(node_id, []):
            next_id = str(segment["to_id"])
            if next_id in visited:
                continue
            next_cost = cost + segment_weight(segment, objective, risk_weight)
            heapq.heappush(
                queue,
                (next_cost, sequence, next_id, visited + (next_id,), path + (segment,)),
            )
            sequence += 1
            expansions += 1
    return paths


def add_coordinate_fallbacks(segments: list[dict[str, Any]]) -> None:
    city_coordinates: dict[str, list[tuple[float, float]]] = defaultdict(list)
    node_coordinates: dict[str, tuple[float, float, str, str, float]] = {}
    for segment in segments:
        for prefix in ("from", "to"):
            latitude = segment.get(f"{prefix}_lat")
            longitude = segment.get(f"{prefix}_lng")
            node_id = str(segment[f"{prefix}_id"])
            city = str(segment.get(f"{prefix}_city") or "").strip().casefold()
            if latitude is not None and longitude is not None:
                coordinates = (float(latitude), float(longitude))
                node_coordinates[node_id] = (
                    *coordinates,
                    str(segment.get(f"{prefix}_coordinate_source") or "legacy database coordinate"),
                    str(segment.get(f"{prefix}_coordinate_status") or "legacy_unverified"),
                    float(segment.get(f"{prefix}_coordinate_confidence") or 0.15),
                )
                if city:
                    city_coordinates[city].append(coordinates)

    for segment in segments:
        for prefix in ("from", "to"):
            node_id = str(segment[f"{prefix}_id"])
            if node_id in node_coordinates:
                continue
            city = str(segment.get(f"{prefix}_city") or "").strip().casefold()
            matches = city_coordinates.get(city, [])
            if matches:
                node_coordinates[node_id] = (
                    sum(item[0] for item in matches) / len(matches),
                    sum(item[1] for item in matches) / len(matches),
                    "city centroid from sourced graph nodes",
                    "estimated",
                    0.25,
                )

    for segment in segments:
        for prefix in ("from", "to"):
            coordinates = node_coordinates.get(str(segment[f"{prefix}_id"]))
            if coordinates:
                segment[f"{prefix}_lat"] = round(coordinates[0], 6)
                segment[f"{prefix}_lng"] = round(coordinates[1], 6)
                segment[f"{prefix}_coordinate_source"] = coordinates[2]
                segment[f"{prefix}_coordinate_status"] = coordinates[3]
                segment[f"{prefix}_coordinate_confidence"] = coordinates[4]


RISK_LABELS = {
    "supplier_risk": "供应商",
    "production_risk": "生产",
    "inventory_risk": "库存",
    "port_congestion_risk": "港口拥堵",
    "transport_delay_risk": "运输延误",
    "country_risk": "国家环境",
    "geopolitical_risk": "地缘政治",
    "trade_risk": "贸易",
    "sanction_risk": "制裁",
    "conflict_risk": "冲突",
    "weather_risk": "天气海况",
    "security_risk": "安全",
    "route_reliability_risk": "路线可靠性",
    "capacity_risk": "运力",
    "news_risk": "实时新闻",
    **FACTOR_LABELS,
}


def format_route(path: list[dict[str, Any]], rank: int) -> dict[str, Any]:
    modes = list(dict.fromkeys(str(segment.get("mode") or "multimodal") for segment in path))
    segment_breakdowns: dict[str, dict[str, Any]] = {}
    segment_risk_providers: dict[str, set[str]] = {}
    for segment in path:
        segment_id = str(segment["segment_id"])
        breakdown = segment.get("risk_breakdown")
        if isinstance(breakdown, str):
            try:
                breakdown = json.loads(breakdown)
            except json.JSONDecodeError:
                breakdown = {}
        parsed_breakdown = breakdown if isinstance(breakdown, dict) else {}
        segment_breakdowns[segment_id] = parsed_breakdown
        providers = {
            str(provider).strip()
            for provider in segment.get("risk_providers") or []
            if str(provider).strip()
        }
        for details in parsed_breakdown.values():
            if not isinstance(details, dict):
                continue
            provider = str(details.get("provider") or "").strip()
            if provider:
                providers.add(provider)
            providers.update(
                str(item).strip()
                for item in details.get("providers") or []
                if str(item).strip()
            )
        segment_risk_providers[segment_id] = providers
    risks = [
        value
        for segment in path
        if segment_risk_providers[str(segment["segment_id"])]
        and (value := optional_float(segment.get("risk_score"))) is not None
    ]
    completeness_values = [
        float(segment.get("risk_data_completeness") or 0.0)
        if segment_risk_providers[str(segment["segment_id"])]
        and optional_float(segment.get("risk_score")) is not None
        else 0.0
        for segment in path
    ]
    risk_values: dict[str, list[float]] = defaultdict(list)
    risk_metadata: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"providers": set(), "evidence": set(), "observed_at": set(), "expires_at": set()}
    )
    missing_factor_keys = {
        str(factor).removesuffix("_risk")
        for segment in path
        for factor in segment.get("risk_missing_factors") or []
    }
    for segment in path:
        breakdown = segment_breakdowns[str(segment["segment_id"])]
        for key, value in breakdown.items():
            normalized_key = str(key).removesuffix("_risk")
            if not isinstance(value, dict) or value.get("value") is None:
                continue
            providers = {
                str(provider).strip()
                for provider in value.get("providers") or []
                if str(provider).strip()
            }
            provider = str(value.get("provider") or "").strip()
            if provider:
                providers.add(provider)
            status = str(value.get("status") or "available").casefold()
            try:
                factor_score = float(value["value"])
            except (TypeError, ValueError):
                missing_factor_keys.add(normalized_key)
                continue
            if 0 <= factor_score <= 1:
                factor_score *= 100
            if not providers or status not in {"available", "partial"} or not 0 <= factor_score <= 100:
                missing_factor_keys.add(normalized_key)
                continue
            risk_values[normalized_key].append(factor_score)
            risk_metadata[normalized_key]["providers"].update(providers)
            risk_metadata[normalized_key]["evidence"].update(
                str(item) for item in value.get("evidence") or [] if item
            )
            observed_at = value.get("observedAt") or value.get("observed_at")
            expires_at = value.get("expiresAt") or value.get("expires_at")
            if observed_at:
                risk_metadata[normalized_key]["observed_at"].add(str(observed_at))
            if expires_at:
                risk_metadata[normalized_key]["expires_at"].add(str(expires_at))
    cost = sum(float(segment.get("cost_usd") or 0.0) for segment in path)
    duration = sum(float(segment.get("time_days") or 0.0) for segment in path)
    distance = sum(float(segment.get("distance_km") or 0.0) for segment in path)
    tags = [f"含{ {'sea': '海运', 'air': '空运', 'rail': '铁路', 'road': '公路', 'truck': '公路'}.get(mode, '多式联运') }" for mode in modes]
    def decoded_geometry(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    return {
        "id": "route-" + "-".join(str(segment["segment_id"]) for segment in path),
        "name": " + ".join(modes).upper() + f" 路线 {rank}",
        "riskScore": round(sum(risks) / len(risks) * 100) if risks else None,
        "riskStatus": "unavailable" if not risks else "available" if len(risks) == len(path) and all(segment.get("risk_status") == "available" for segment in path) else "partial",
        "riskDataCompleteness": round(sum(completeness_values) / len(completeness_values), 4) if completeness_values else 0.0,
        "riskKnownLegs": len(risks),
        "riskMissingFactors": sorted({str(factor) for segment in path for factor in segment.get("risk_missing_factors") or []}),
        "riskProviders": sorted({str(provider) for segment in path for provider in segment.get("risk_providers") or []}),
        "cost": round(cost, 2),
        "durationDays": round(duration, 2),
        "distanceKm": round(distance, 2),
        "tags": tags,
        "riskFactors": [
            {
                "key": key,
                "label": RISK_LABELS.get(key, key),
                "score": round(sum(values) / len(values), 2),
                "status": "available",
                "provider": next(iter(sorted(risk_metadata[key]["providers"])), None),
                "providers": sorted(risk_metadata[key]["providers"]),
                "observedAt": max(risk_metadata[key]["observed_at"], default=None),
                "expiresAt": min(risk_metadata[key]["expires_at"], default=None),
                "evidence": sorted(risk_metadata[key]["evidence"]),
                "detail": f"仅聚合带真实 Provider 的{RISK_LABELS.get(key, key)}风险观测",
            }
            for key, values in sorted(risk_values.items(), key=lambda item: sum(item[1]) / len(item[1]), reverse=True)
        ]
        + [
            {
                "key": key,
                "label": RISK_LABELS.get(key, key),
                "score": None,
                "status": "unavailable",
                "provider": None,
                "providers": [],
                "observedAt": None,
                "expiresAt": None,
                "evidence": [],
                "detail": f"{RISK_LABELS.get(key, key)}缺少有效 Provider 或证据，未参与评分",
            }
            for key in sorted(missing_factor_keys - set(risk_values))
        ],
        "legs": [
            {
                "from": {
                    "id": segment.get("from_location_id") or segment["from_id"],
                    "name": str(segment.get("from_name") or segment.get("from_city") or segment["from_id"]),
                    "city": segment.get("from_city"),
                    "country": segment.get("from_country"),
                    "countryCode": segment.get("from_country_code"),
                    "countryNameZh": segment.get("from_country_name_zh"),
                    "lat": segment.get("from_lat"),
                    "lng": segment.get("from_lng"),
                    "coordinateSource": segment.get("from_coordinate_source") or "unavailable",
                    "coordinateStatus": segment.get("from_coordinate_status") or "unavailable",
                    "coordinateConfidence": segment.get("from_coordinate_confidence") or 0.0,
                },
                "to": {
                    "id": segment.get("to_location_id") or segment["to_id"],
                    "name": str(segment.get("to_name") or segment.get("to_city") or segment["to_id"]),
                    "city": segment.get("to_city"),
                    "country": segment.get("to_country"),
                    "countryCode": segment.get("to_country_code"),
                    "countryNameZh": segment.get("to_country_name_zh"),
                    "lat": segment.get("to_lat"),
                    "lng": segment.get("to_lng"),
                    "coordinateSource": segment.get("to_coordinate_source") or "unavailable",
                    "coordinateStatus": segment.get("to_coordinate_status") or "unavailable",
                    "coordinateConfidence": segment.get("to_coordinate_confidence") or 0.0,
                },
                "mode": segment.get("mode"),
                "cost": round(float(segment.get("cost_usd") or 0.0), 2),
                "durationDays": round(float(segment.get("time_days") or 0.0), 2),
                "distanceKm": round(float(segment.get("distance_km") or 0.0), 2),
                "riskScore": round(risk_score * 100)
                if segment_risk_providers[str(segment["segment_id"])]
                and (risk_score := optional_float(segment.get("risk_score"))) is not None
                else None,
                "riskStatus": (segment.get("risk_status") or "unavailable")
                if segment_risk_providers[str(segment["segment_id"])]
                else "unavailable",
                "riskDataCompleteness": float(segment.get("risk_data_completeness") or 0.0)
                if segment_risk_providers[str(segment["segment_id"])]
                else 0.0,
                "riskMissingFactors": segment.get("risk_missing_factors") or [],
                "riskProviders": sorted(segment_risk_providers[str(segment["segment_id"])]),
                "geometry": decoded_geometry(segment.get("geometry_geojson")),
                "geometrySource": segment.get("geometry_source"),
                "geometryStatus": segment.get("geometry_status") or "unavailable",
                "geometryConfidence": float(segment.get("geometry_confidence") or 0.0),
                "feasibilityStatus": segment.get("feasibility_status") or "unverified",
                "spatialExposures": segment.get("spatial_exposures") or [],
                "newsRiskScore": round(float(segment.get("news_risk_score") or 0.0) * 100),
                "newsRiskZones": segment.get("news_risk_zones") or [],
                "weatherRiskScore": round(float(segment["route_weather_risk"]), 1) if segment.get("route_weather_risk") is not None else None,
                "weatherRiskStatus": segment.get("route_weather_status") or "unavailable",
                "weatherRiskConfidence": segment.get("route_weather_confidence"),
                "weatherDataCompleteness": segment.get("route_weather_data_completeness"),
                "weatherSamplingMethod": segment.get("route_weather_sampling_method") or "unavailable",
                "weatherEvidence": segment.get("route_weather_evidence") or [],
                "weatherUpdatedAt": segment.get("route_weather_updated_at"),
                "weatherExpiresAt": segment.get("route_weather_expires_at"),
                "aisCongestionScore": round(float(segment["ais_congestion_score"]), 1) if segment.get("ais_congestion_score") is not None else None,
                "aisCongestionStatus": segment.get("ais_congestion_status") or "unavailable",
                "aisCongestionConfidence": segment.get("ais_congestion_confidence"),
                "aisCongestionDataCompleteness": segment.get("ais_congestion_data_completeness"),
                "aisCongestionEvidence": segment.get("ais_congestion_evidence") or [],
                "aisCongestionObservedAt": segment.get("ais_congestion_observed_at"),
                "aisCongestionExpiresAt": segment.get("ais_congestion_expires_at"),
            }
            for segment in path
        ],
    }
