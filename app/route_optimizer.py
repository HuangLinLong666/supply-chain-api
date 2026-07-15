"""Shortest-path helpers for scored supply-chain route segments."""

from __future__ import annotations

import heapq
import json
from collections import defaultdict
from typing import Any


def segment_weight(segment: dict[str, Any], objective: str, risk_weight: float) -> float:
    risk_score = float(segment.get("risk_score") or 0.5)
    cost_score = float(segment.get("cost_score") or 0.5)
    if objective == "min_risk":
        return risk_score
    if objective == "min_cost":
        return max(float(segment.get("cost_usd") or 0.0), 0.000001)
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
    risk_scores = [float(segment.get("risk_score") or 0.5) for segment in path_segments]
    return {
        "objective": objective,
        "optimization_score": round(distances[destination], 6),
        "total_cost_usd": round(total_cost, 2),
        "total_time_days": round(total_time, 2),
        "average_risk_score": round(sum(risk_scores) / len(risk_scores), 4),
        "maximum_risk_score": round(max(risk_scores), 4),
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

    queue: list[tuple[float, int, str, tuple[str, ...], tuple[dict[str, Any], ...]]] = []
    sequence = 0
    for origin in origins:
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
    node_coordinates: dict[str, tuple[float, float, str]] = {}
    for segment in segments:
        for prefix in ("from", "to"):
            latitude = segment.get(f"{prefix}_lat")
            longitude = segment.get(f"{prefix}_lng")
            node_id = str(segment[f"{prefix}_id"])
            city = str(segment.get(f"{prefix}_city") or "").strip().casefold()
            if latitude is not None and longitude is not None:
                coordinates = (float(latitude), float(longitude))
                node_coordinates[node_id] = (*coordinates, "database")
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
                    "city_estimate",
                )

    for _ in range(12):
        additions: dict[str, tuple[float, float, str]] = {}
        neighbor_coordinates: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for segment in segments:
            from_id = str(segment["from_id"])
            to_id = str(segment["to_id"])
            if from_id in node_coordinates:
                neighbor_coordinates[to_id].append(node_coordinates[from_id][:2])
            if to_id in node_coordinates:
                neighbor_coordinates[from_id].append(node_coordinates[to_id][:2])
        for node_id, matches in neighbor_coordinates.items():
            if node_id not in node_coordinates and matches:
                additions[node_id] = (
                    sum(item[0] for item in matches) / len(matches),
                    sum(item[1] for item in matches) / len(matches),
                    "graph_neighbor_estimate",
                )
        if not additions:
            break
        node_coordinates.update(additions)

    for segment in segments:
        for prefix in ("from", "to"):
            coordinates = node_coordinates.get(str(segment[f"{prefix}_id"]))
            if coordinates:
                segment[f"{prefix}_lat"] = round(coordinates[0], 6)
                segment[f"{prefix}_lng"] = round(coordinates[1], 6)
                segment[f"{prefix}_coordinate_source"] = coordinates[2]


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
}


def format_route(path: list[dict[str, Any]], rank: int) -> dict[str, Any]:
    modes = list(dict.fromkeys(str(segment.get("mode") or "multimodal") for segment in path))
    risks = [float(segment.get("risk_score") or 0.5) for segment in path]
    risk_values: dict[str, list[float]] = defaultdict(list)
    for segment in path:
        breakdown = segment.get("risk_breakdown")
        if isinstance(breakdown, str):
            try:
                breakdown = json.loads(breakdown)
            except json.JSONDecodeError:
                breakdown = {}
        for key, value in (breakdown or {}).items():
            if isinstance(value, dict) and value.get("value") is not None:
                risk_values[key].append(float(value["value"]))
        news_risk = float(segment.get("news_risk_score") or 0.0)
        if news_risk > 0:
            risk_values["news_risk"].append(news_risk)

    cost = sum(float(segment.get("cost_usd") or 0.0) for segment in path)
    duration = sum(float(segment.get("time_days") or 0.0) for segment in path)
    distance = sum(float(segment.get("distance_km") or 0.0) for segment in path)
    tags = [f"含{ {'sea': '海运', 'air': '空运', 'rail': '铁路', 'road': '公路', 'truck': '公路'}.get(mode, '多式联运') }" for mode in modes]
    return {
        "id": "route-" + "-".join(str(segment["segment_id"]) for segment in path),
        "name": " + ".join(modes).upper() + f" 路线 {rank}",
        "riskScore": round(sum(risks) / len(risks) * 100),
        "cost": round(cost, 2),
        "durationDays": round(duration, 2),
        "distanceKm": round(distance, 2),
        "tags": tags,
        "riskFactors": [
            {
                "key": key.removesuffix("_risk"),
                "label": RISK_LABELS.get(key, key),
                "score": round(sum(values) / len(values) * 100),
                "detail": f"该路线各分段{RISK_LABELS.get(key, key)}风险平均值",
            }
            for key, values in sorted(risk_values.items(), key=lambda item: sum(item[1]) / len(item[1]), reverse=True)
        ],
        "legs": [
            {
                "from": {
                    "id": segment["from_id"],
                    "name": segment.get("from_name"),
                    "city": segment.get("from_city"),
                    "country": segment.get("from_country"),
                    "lat": segment.get("from_lat"),
                    "lng": segment.get("from_lng"),
                    "coordinateSource": segment.get("from_coordinate_source", "unavailable"),
                },
                "to": {
                    "id": segment["to_id"],
                    "name": segment.get("to_name"),
                    "city": segment.get("to_city"),
                    "country": segment.get("to_country"),
                    "lat": segment.get("to_lat"),
                    "lng": segment.get("to_lng"),
                    "coordinateSource": segment.get("to_coordinate_source", "unavailable"),
                },
                "mode": segment.get("mode"),
                "cost": round(float(segment.get("cost_usd") or 0.0), 2),
                "durationDays": round(float(segment.get("time_days") or 0.0), 2),
                "distanceKm": round(float(segment.get("distance_km") or 0.0), 2),
                "riskScore": round(float(segment.get("risk_score") or 0.5) * 100),
                "newsRiskScore": round(float(segment.get("news_risk_score") or 0.0) * 100),
                "newsRiskZones": segment.get("news_risk_zones") or [],
            }
            for segment in path
        ],
    }
