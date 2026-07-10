"""Shortest-path helpers for scored supply-chain route segments."""

from __future__ import annotations

import heapq
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
