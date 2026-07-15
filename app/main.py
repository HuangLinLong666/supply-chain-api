"""Minimal read-only API for the AuraDB supply-chain graph."""

from __future__ import annotations

import os
import time
import json
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.route_optimizer import add_coordinate_fallbacks, format_route, k_shortest_paths, shortest_path
from database.neo4j_client import close_driver, get_settings, run_query, verify_connectivity
from weather.config import WeatherSettings
from weather.service import update_ports
from gdelt.config import GdeltSettings
from gdelt.service import update_news_risk


_route_graph_cache: tuple[float, list[dict[str, Any]]] | None = None
ROUTE_GRAPH_CACHE_SECONDS = 300


def cors_origins() -> list[str]:
    raw = os.getenv("API_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(_: FastAPI):
    from weather.scheduler import start_scheduler, stop_scheduler
    start_scheduler()
    yield
    stop_scheduler()
    close_driver()


app = FastAPI(
    title="Supply Chain Graph API",
    description="Read-only API for querying the Neo4j AuraDB supply-chain graph.",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def safe_query(query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    try:
        return run_query(query, parameters)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/", tags=["Service"], summary="API service information")
def root() -> dict[str, str]:
    return {
        "service": "Supply Chain Graph API",
        "status": "ok",
        "documentation": "/docs",
        "openapi": "/openapi.json",
        "health": "/health",
    }


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "database": settings.database,
        "uri_host": settings.uri.split("://", 1)[-1],
    }


@app.get("/health/aura")
def aura_health() -> dict[str, str]:
    try:
        verify_connectivity()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "aura": "connected"}


@app.get("/api/graph/summary")
def graph_summary() -> dict[str, list[dict[str, Any]]]:
    nodes = safe_query(
        """
        MATCH (n)
        RETURN labels(n) AS labels, count(n) AS count
        ORDER BY count DESC
        """
    )
    relationships = safe_query(
        """
        MATCH ()-[r]->()
        RETURN type(r) AS type, count(r) AS count
        ORDER BY count DESC
        """
    )
    return {"nodes": nodes, "relationships": relationships}


@app.get("/api/supply-chain/routes")
def supply_chain_routes(limit: int = 25) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    routes = safe_query(
        """
        MATCH (segment:RouteSegment)
        OPTIONAL MATCH (route:Route)-[routeRel:HAS_SEGMENT]->(segment)
        OPTIONAL MATCH (segment)-[:FROM|FROM_NODE]->(fromNode)
        OPTIONAL MATCH (segment)-[:TO|TO_NODE]->(toNode)
        RETURN
          coalesce(route.route_id, route.routeId, segment.route_id, segment.routeId, "unknown") AS route_id,
          coalesce(segment.segment_id, segment.segmentId, elementId(segment)) AS segment_id,
          coalesce(routeRel.sequence, segment.sequence, segment.legNumber) AS sequence,
          labels(fromNode) AS from_labels,
          properties(fromNode) AS from_properties,
          labels(toNode) AS to_labels,
          properties(toNode) AS to_properties,
          properties(segment) AS segment_properties
        ORDER BY route_id, sequence
        LIMIT $limit
        """,
        {"limit": limit},
    )
    return {"routes": routes}


@app.get("/api/risk/overview")
def risk_overview(limit: int = 25) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    risk_labels = ["RiskFactor", "RiskEvent", "Country", "Port", "RouteSegment"]
    counts = safe_query(
        """
        MATCH (n)
        WHERE any(label IN labels(n) WHERE label IN $risk_labels)
        UNWIND labels(n) AS label
        WITH label, n
        WHERE label IN $risk_labels
        RETURN label, count(n) AS count
        ORDER BY count DESC
        """,
        {"risk_labels": risk_labels},
    )
    countries = safe_query(
        """
        MATCH (c:Country)
        RETURN
          coalesce(c.name, c.country, c.iso2, c.iso3) AS name,
          c.iso2 AS iso2,
          c.iso3 AS iso3,
          c.geopoliticalRisk AS geopoliticalRisk,
          c.tradeRisk AS tradeRisk,
          c.sanction_risk AS sanction_risk,
          c.conflict_risk AS conflict_risk,
          properties(c) AS properties
        LIMIT $limit
        """,
        {"limit": limit},
    )
    ports = safe_query(
        """
        MATCH (p:Port)
        RETURN
          coalesce(p.name, p.portName, p.unlocode, p.code) AS name,
          coalesce(p.unlocode, p.code) AS code,
          p.congestionRisk AS congestionRisk,
          p.congestion_score AS congestion_score,
          p.avg_wait_time_hours AS avg_wait_time_hours,
          properties(p) AS properties
        LIMIT $limit
        """,
        {"limit": limit},
    )
    route_segments = safe_query(
        """
        MATCH (s:RouteSegment)
        RETURN
          coalesce(s.segment_id, s.segmentId, elementId(s)) AS segment_id,
          coalesce(s.mode, s.routeMode) AS mode,
          coalesce(s.base_risk_score, s.riskScore, s.costRiskScore) AS risk_score,
          coalesce(s.estimated_cost_usd, s.totalCostUSD) AS estimated_cost_usd,
          coalesce(s.estimated_time_days, s.estimatedTimeHours) AS estimated_time,
          properties(s) AS properties
        ORDER BY risk_score DESC
        LIMIT $limit
        """,
        {"limit": limit},
    )
    return {
        "counts": counts,
        "countries": countries,
        "ports": ports,
        "route_segments": route_segments,
    }


@app.get(
    "/api/risk/segments",
    tags=["Risk & Cost"],
    summary="Rank route segments by comprehensive risk",
)
def ranked_risk_segments(
    limit: int = Query(25, ge=1, le=100),
    minimum_risk: float = Query(0.0, ge=0.0, le=1.0),
) -> dict[str, Any]:
    segments = safe_query(
        """
        MATCH (segment:RouteSegment)-[:FROM_NODE]->(fromNode)
        MATCH (segment)-[:TO_NODE]->(toNode)
        WITH segment, fromNode, toNode,
             coalesce(segment.total_risk_score, segment.riskScore, segment.base_risk_score, 0.0) AS riskScore
        WHERE riskScore >= $minimum_risk
        RETURN
          coalesce(segment.segmentId, segment.segment_id, elementId(segment)) AS segment_id,
          coalesce(fromNode.name, fromNode.code, fromNode.id, segment.fromNodeName) AS origin,
          coalesce(toNode.name, toNode.code, toNode.id, segment.toNodeName) AS destination,
          coalesce(segment.mode, segment.routeMode) AS mode,
          riskScore AS comprehensive_risk_score,
          segment.risk_breakdown AS risk_breakdown,
          segment.risk_explanation AS risk_explanation,
          coalesce(segment.confidence_score, 0.0) AS confidence_score,
          coalesce(segment.estimated_cost_usd, segment.baseCostUSD, 0.0) AS estimated_cost_usd
        ORDER BY comprehensive_risk_score DESC
        LIMIT $limit
        """,
        {"minimum_risk": minimum_risk, "limit": limit},
    )
    return {"count": len(segments), "segments": segments}


@app.get(
    "/api/cost/segments",
    tags=["Risk & Cost"],
    summary="Rank route segments by estimated cost",
)
def ranked_cost_segments(
    order: Literal["asc", "desc"] = Query("asc", description="asc returns the lowest-cost segments first"),
    limit: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    order_clause = "ASC" if order == "asc" else "DESC"
    segments = safe_query(
        f"""
        MATCH (segment:RouteSegment)-[:FROM_NODE]->(fromNode)
        MATCH (segment)-[:TO_NODE]->(toNode)
        RETURN
          coalesce(segment.segmentId, segment.segment_id, elementId(segment)) AS segment_id,
          coalesce(fromNode.name, fromNode.code, fromNode.id, segment.fromNodeName) AS origin,
          coalesce(toNode.name, toNode.code, toNode.id, segment.toNodeName) AS destination,
          coalesce(segment.mode, segment.routeMode) AS mode,
          coalesce(segment.estimated_cost_usd, segment.baseCostUSD, 0.0) AS estimated_cost_usd,
          coalesce(segment.costScore, 0.0) AS normalized_cost_score,
          coalesce(segment.costRiskScore, 0.0) AS cost_risk_score,
          coalesce(segment.estimated_time_days, segment.estimatedTimeHours / 24.0, 0.0) AS estimated_time_days
        ORDER BY estimated_cost_usd {order_clause}
        LIMIT $limit
        """,
        {"limit": limit},
    )
    return {"count": len(segments), "order": order, "segments": segments}


@app.get(
    "/api/routes/nodes",
    tags=["Route Optimization"],
    summary="List selectable route origin and destination nodes",
)
def route_nodes(search: str | None = Query(None), limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    nodes = safe_query(
        """
        MATCH (segment:RouteSegment)-[:FROM_NODE|TO_NODE]->(node)
        WITH DISTINCT node
        WITH node, coalesce(node.name, node.code, node.id, elementId(node)) AS name
        WHERE $search IS NULL OR toLower(toString(name)) CONTAINS toLower($search)
        RETURN elementId(node) AS node_id, name, labels(node) AS labels
        ORDER BY name
        LIMIT $limit
        """,
        {"search": search, "limit": limit},
    )
    return {"count": len(nodes), "nodes": nodes}


def route_graph_segments() -> list[dict[str, Any]]:
    global _route_graph_cache
    now = time.monotonic()
    if _route_graph_cache is not None and now - _route_graph_cache[0] < ROUTE_GRAPH_CACHE_SECONDS:
        return _route_graph_cache[1]
    segments = safe_query(
        """
        MATCH (segment:RouteSegment)-[:FROM_NODE]->(fromNode)
        MATCH (segment)-[:TO_NODE]->(toNode)
        RETURN
          elementId(fromNode) AS from_id,
          coalesce(fromNode.name, fromNode.code, fromNode.id, segment.fromNodeName) AS from_name,
          fromNode.city AS from_city,
          fromNode.country AS from_country,
          fromNode.latitude AS from_lat,
          fromNode.longitude AS from_lng,
          labels(fromNode) AS from_labels,
          elementId(toNode) AS to_id,
          coalesce(toNode.name, toNode.code, toNode.id, segment.toNodeName) AS to_name,
          toNode.city AS to_city,
          toNode.country AS to_country,
          toNode.latitude AS to_lat,
          toNode.longitude AS to_lng,
          labels(toNode) AS to_labels,
          coalesce(segment.segmentId, segment.segment_id, elementId(segment)) AS segment_id,
          coalesce(segment.mode, segment.routeMode) AS mode,
          CASE WHEN segment.news_risk_expires_at > datetime()
               THEN coalesce(segment.dynamic_risk_score, segment.total_risk_score, segment.riskScore, segment.base_risk_score, 0.5)
               ELSE coalesce(segment.total_risk_score, segment.riskScore, segment.base_risk_score, 0.5)
          END AS risk_score,
          coalesce(segment.estimated_cost_usd, segment.baseCostUSD, 0.0) AS cost_usd,
          coalesce(segment.costScore, 0.5) AS cost_score,
          coalesce(segment.costRiskScore, 0.5) AS cost_risk_score,
          coalesce(segment.estimated_time_days, segment.estimatedTimeHours / 24.0, 0.0) AS time_days,
          coalesce(segment.distance_km, segment.distanceKm, 0.0) AS distance_km,
          segment.risk_explanation AS risk_explanation,
          segment.risk_breakdown AS risk_breakdown,
          CASE WHEN segment.news_risk_expires_at > datetime() THEN coalesce(segment.news_risk_score,0.0) ELSE 0.0 END AS news_risk_score,
          CASE WHEN segment.news_risk_expires_at > datetime() THEN segment.news_risk_zones ELSE [] END AS news_risk_zones
        """
    )
    add_coordinate_fallbacks(segments)
    _route_graph_cache = (now, segments)
    return segments


def matching_node_ids(segments: list[dict[str, Any]], value: str) -> set[str]:
    expected = value.strip().casefold()
    exact_matches: set[str] = set()
    partial_matches: set[str] = set()
    for segment in segments:
        for prefix in ("from", "to"):
            node_id = str(segment[f"{prefix}_id"])
            values = {
                node_id,
                str(segment.get(f"{prefix}_name") or ""),
                str(segment.get(f"{prefix}_city") or ""),
            }
            normalized = {item.strip().casefold() for item in values if item.strip()}
            if expected in normalized:
                exact_matches.add(node_id)
            elif any(expected in item for item in normalized):
                partial_matches.add(node_id)
    return exact_matches or partial_matches


def without_high_news_risk(segments: list[dict[str, Any]], threshold: float = 0.6) -> tuple[list[dict[str, Any]], list[str]]:
    blocked = [segment for segment in segments if float(segment.get("news_risk_score") or 0.0) >= threshold]
    blocked_zones = sorted({zone for segment in blocked for zone in segment.get("news_risk_zones", [])})
    blocked_ids = {str(segment["segment_id"]) for segment in blocked}
    return [segment for segment in segments if str(segment["segment_id"]) not in blocked_ids], blocked_zones


def high_risk_zones_in_path(path: list[dict[str, Any]], threshold: float = 0.6) -> list[str]:
    return sorted({
        zone
        for segment in path
        if float(segment.get("news_risk_score") or 0.0) >= threshold
        for zone in segment.get("news_risk_zones", [])
    })


@app.get(
    "/api/suppliers",
    tags=["Route Planning"],
    summary="List suppliers available for route planning",
)
def suppliers(search: str | None = Query(None), limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    rows = safe_query(
        """
        MATCH (supplier:Supplier)
        OPTIONAL MATCH (segment:RouteSegment)-[:FROM_NODE]->(supplier)
        WITH supplier, count(segment) AS routeCount
        WHERE $search IS NULL
           OR toLower(coalesce(supplier.name, '')) CONTAINS toLower($search)
           OR toLower(coalesce(supplier.supplier_id, supplier.supplierCode, '')) CONTAINS toLower($search)
        RETURN
          coalesce(supplier.supplier_id, supplier.supplierCode, elementId(supplier)) AS id,
          supplier.name AS name,
          supplier.city AS city,
          supplier.country AS country,
          coalesce(supplier.total_risk_score, supplier.supplier_risk, 0.5) AS riskScore,
          supplier.risk_explanation AS riskExplanation,
          routeCount AS routeCount
        ORDER BY routeCount DESC, name
        LIMIT $limit
        """,
        {"search": search, "limit": limit},
    )
    return {"count": len(rows), "suppliers": rows}


@app.get(
    "/api/cities",
    tags=["Route Planning"],
    summary="List origin and destination cities or named route nodes",
)
def cities(search: str | None = Query(None), limit: int = Query(200, ge=1, le=500)) -> dict[str, Any]:
    rows = safe_query(
        """
        MATCH (:RouteSegment)-[:FROM_NODE|TO_NODE]->(node)
        WITH DISTINCT node,
             coalesce(node.city, node.name, node.code, node.id, elementId(node)) AS value,
             coalesce(node.name, node.code, node.id, elementId(node)) AS name
        WHERE $search IS NULL OR toLower(toString(value)) CONTAINS toLower($search)
        RETURN value AS id, value, name, node.city AS city, node.country AS country,
               node.latitude AS lat, node.longitude AS lng, labels(node) AS labels
        ORDER BY value, name
        LIMIT $limit
        """,
        {"search": search, "limit": limit},
    )
    return {"count": len(rows), "cities": rows}


@app.get(
    "/api/routes/recommend",
    tags=["Route Planning"],
    summary="Query multiple complete routes by supplier, origin, and destination",
)
def recommend_routes(
    supplier: str = Query(..., description="Supplier ID or name, for example CATL or SUP-CATL"),
    origin: str = Query(..., description="Origin node ID, node name, or city"),
    destination: str = Query(..., description="Destination node ID, node name, or city"),
    limit: int = Query(5, ge=1, le=10),
    risk_weight: float = Query(0.5, ge=0.0, le=1.0),
    max_hops: int = Query(12, ge=1, le=20),
    auto_reroute: bool = Query(True, description="Prefer routes that avoid active HIGH/CRITICAL news-risk zones"),
) -> dict[str, Any]:
    supplier_rows = suppliers(search=supplier, limit=20)["suppliers"]
    exact_supplier = next(
        (
            row
            for row in supplier_rows
            if supplier.casefold() in {str(row.get("id", "")).casefold(), str(row.get("name", "")).casefold()}
        ),
        supplier_rows[0] if supplier_rows else None,
    )
    if exact_supplier is None:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier!r} was not found")

    segments = route_graph_segments()
    origin_ids = matching_node_ids(segments, origin)
    destination_ids = matching_node_ids(segments, destination)
    if not origin_ids:
        raise HTTPException(status_code=404, detail=f"Origin {origin!r} was not found in the route network")
    if not destination_ids:
        raise HTTPException(status_code=404, detail=f"Destination {destination!r} was not found in the route network")

    candidate_segments, _ = without_high_news_risk(segments) if auto_reroute else (segments, [])
    baseline_candidates = k_shortest_paths(segments, origin_ids, destination_ids, "balanced", risk_weight, 1, max_hops)
    candidates = k_shortest_paths(
        candidate_segments,
        origin_ids,
        destination_ids,
        "balanced",
        risk_weight,
        limit * 2,
        max_hops,
    )

    avoided_zones = high_risk_zones_in_path(baseline_candidates[0]) if baseline_candidates else []
    baseline_signature = tuple(segment["segment_id"] for segment in baseline_candidates[0]) if baseline_candidates else ()
    safe_signature = tuple(segment["segment_id"] for segment in candidates[0]) if candidates else ()
    rerouted = bool(candidates and avoided_zones and baseline_signature != safe_signature)
    if not candidates and candidate_segments is not segments:
        candidates = k_shortest_paths(segments, origin_ids, destination_ids, "balanced", risk_weight, limit * 2, max_hops)
        rerouted = False

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail="No directed RouteSegment path connects the selected origin and destination",
        )

    formatted = [format_route(path, index + 1) for index, path in enumerate(candidates)]
    supplier_risk = float(exact_supplier.get("riskScore") or 0.5)
    for route in formatted:
        route["riskScore"] = round(0.2 * supplier_risk * 100 + 0.8 * route["riskScore"])
        route["riskFactors"].insert(
            0,
            {
                "key": "supplier",
                "label": "供应商",
                "score": round(supplier_risk * 100),
                "detail": exact_supplier.get("riskExplanation") or f"供应商 {exact_supplier['name']} 综合风险",
            },
        )
    maximum_cost = max(item["cost"] for item in formatted) or 1.0
    formatted.sort(
        key=lambda route: risk_weight * route["riskScore"] / 100
        + (1 - risk_weight) * route["cost"] / maximum_cost
    )
    routes = formatted[:limit]
    if routes:
        min(routes, key=lambda route: route["cost"])["tags"].insert(0, "成本最优")
        min(routes, key=lambda route: route["riskScore"])["tags"].insert(0, "风险最优")
        min(routes, key=lambda route: route["durationDays"])["tags"].insert(0, "时效最优")
    return {
        "query": {
            "supplier": exact_supplier,
            "origin": origin,
            "destination": destination,
            "riskWeight": risk_weight,
            "autoReroute": auto_reroute,
        },
        "dynamicRouting": {"rerouted": rerouted, "avoidedZones": avoided_zones if rerouted else [], "fallbackUsed": bool(avoided_zones and not rerouted)},
        "count": len(routes),
        "routes": routes,
    }


@app.get(
    "/api/routes/optimize",
    tags=["Route Optimization"],
    summary="Recommend the minimum-cost, minimum-risk, or balanced path",
)
def optimize_route(
    origin_id: str = Query(..., description="node_id returned by GET /api/routes/nodes"),
    destination_id: str = Query(..., description="node_id returned by GET /api/routes/nodes"),
    objective: Literal["min_cost", "min_risk", "balanced"] = Query("balanced"),
    risk_weight: float = Query(0.5, ge=0.0, le=1.0, description="Only used for balanced optimization"),
    auto_reroute: bool = Query(True, description="Avoid active HIGH/CRITICAL news-risk segments when an alternative exists"),
) -> dict[str, Any]:
    if origin_id == destination_id:
        raise HTTPException(status_code=400, detail="origin_id and destination_id must be different")
    segments = route_graph_segments()
    candidate_segments, _ = without_high_news_risk(segments) if auto_reroute else (segments, [])
    baseline_result = shortest_path(segments, origin_id, destination_id, objective, risk_weight)
    result = shortest_path(candidate_segments, origin_id, destination_id, objective, risk_weight)
    avoided_zones = high_risk_zones_in_path(baseline_result["segments"]) if baseline_result else []
    baseline_signature = tuple(segment["segment_id"] for segment in baseline_result["segments"]) if baseline_result else ()
    safe_signature = tuple(segment["segment_id"] for segment in result["segments"]) if result else ()
    rerouted = bool(result and avoided_zones and baseline_signature != safe_signature)
    if result is None and candidate_segments is not segments:
        result = shortest_path(segments, origin_id, destination_id, objective, risk_weight)
        rerouted = False
    if result is None:
        raise HTTPException(status_code=404, detail="No directed RouteSegment path connects the selected nodes")
    result["origin_id"] = origin_id
    result["destination_id"] = destination_id
    result["risk_weight"] = risk_weight if objective == "balanced" else None
    result["dynamic_routing"] = {"rerouted": rerouted, "avoided_zones": avoided_zones if rerouted else [], "fallback_used": bool(avoided_zones and not rerouted)}
    return result


@app.get(
    "/api/routes/recommendations",
    tags=["Route Optimization"],
    summary="Rank complete predefined routes by cost, risk, or balanced score",
)
def route_recommendations(
    objective: Literal["min_cost", "min_risk", "balanced"] = Query("balanced"),
    risk_weight: float = Query(0.5, ge=0.0, le=1.0),
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    routes = safe_query(
        """
        MATCH (route:Route)-[membership:HAS_SEGMENT]->(segment:RouteSegment)
        WITH route, segment, membership,
             coalesce(segment.total_risk_score, segment.riskScore, segment.base_risk_score, 0.5) AS risk,
             coalesce(segment.estimated_cost_usd, segment.baseCostUSD, 0.0) AS cost,
             coalesce(segment.costScore, 0.5) AS costScore
        ORDER BY coalesce(membership.sequence, segment.sequence, 0)
        WITH route,
             collect({
               segment_id: coalesce(segment.segmentId, segment.segment_id, elementId(segment)),
               sequence: coalesce(membership.sequence, segment.sequence),
               origin: segment.fromNodeName,
               destination: segment.toNodeName,
               mode: coalesce(segment.mode, segment.routeMode),
               risk_score: risk,
               estimated_cost_usd: cost
             }) AS segments,
             sum(cost) AS totalCost,
             avg(risk) AS averageRisk,
             max(risk) AS maximumRisk,
             avg(costScore) AS averageCostScore,
             sum(coalesce(segment.estimated_time_days, segment.estimatedTimeHours / 24.0, 0.0)) AS totalTime
        WITH route, segments, totalCost, averageRisk, maximumRisk, averageCostScore, totalTime,
             CASE $objective
               WHEN 'min_cost' THEN totalCost
               WHEN 'min_risk' THEN averageRisk
               ELSE $risk_weight * averageRisk + (1.0 - $risk_weight) * averageCostScore
             END AS optimizationScore
        RETURN
          coalesce(route.route_id, route.routeId, elementId(route)) AS route_id,
          route.name AS name,
          optimizationScore AS optimization_score,
          totalCost AS total_cost_usd,
          averageRisk AS average_risk_score,
          maximumRisk AS maximum_risk_score,
          totalTime AS total_time_days,
          segments
        ORDER BY optimization_score ASC
        LIMIT $limit
        """,
        {"objective": objective, "risk_weight": risk_weight, "limit": limit},
    )
    return {"objective": objective, "risk_weight": risk_weight, "count": len(routes), "routes": routes}


@app.get("/api/ports/weather-risks", tags=["Port Weather"])
def port_weather_risks(risk_level: str | None=None, country: str | None=None, min_score: float=0, updated_after: str | None=None, page: int=1, page_size: int=50, sort_by: Literal["score","updated_at","name"]="score", sort_order: Literal["asc","desc"]="desc") -> dict[str,Any]:
    page=max(page,1); page_size=max(1,min(page_size,200)); order={"score":"p.weather_risk_score","updated_at":"p.weather_updated_at","name":"p.name"}[sort_by]; direction="ASC" if sort_order=="asc" else "DESC"
    rows=safe_query(f"""MATCH (p:Port) WHERE p.weather_risk_score IS NOT NULL AND ($level IS NULL OR p.weather_risk_level=$level) AND ($country IS NULL OR toLower(p.country)=toLower($country)) AND p.weather_risk_score >= $min_score AND ($updated_after IS NULL OR p.weather_updated_at >= datetime($updated_after)) RETURN coalesce(p.unlocode,p.code,p['port_id'],elementId(p)) AS portId,p.name AS portName,p.country AS country,p.latitude AS latitude,p.longitude AS longitude,p.weather_risk_score AS score,p.weather_risk_level AS level,p.weather_risk_confidence AS confidence,p.weather_data_completeness AS dataCompleteness,p.weather_risk_trend AS trend,p.weather_risk_summary AS summary,p.weather_updated_at AS updatedAt ORDER BY {order} {direction} SKIP $skip LIMIT $limit""",{"level":risk_level,"country":country,"min_score":min_score,"updated_after":updated_after,"skip":(page-1)*page_size,"limit":page_size})
    return {"page":page,"pageSize":page_size,"count":len(rows),"ports":rows}


@app.get("/api/ports/weather-risks/high", tags=["Port Weather"])
def high_risk_ports() -> dict[str,Any]: return port_weather_risks(min_score=50,page_size=200)


@app.get("/api/ports/{port_id}/weather", tags=["Port Weather"])
def port_weather(port_id: str) -> dict[str,Any]:
    rows=safe_query("""MATCH (p:Port) WHERE coalesce(p.unlocode,p.code,p['port_id'],elementId(p))=$id OPTIONAL MATCH (p)-[:HAS_WEATHER_SNAPSHOT]->(w:WeatherRiskSnapshot) WITH p,w ORDER BY w.observed_at DESC LIMIT 1 RETURN coalesce(p.unlocode,p.code,p['port_id'],elementId(p)) AS portId,p.name AS portName,p.country AS country,{latitude:p.latitude,longitude:p.longitude} AS coordinates,{temperatureC:p.current_temperature_c,relativeHumidity:p.current_relative_humidity,precipitationMm:p.current_precipitation_mm,visibilityM:p.current_visibility_m,windSpeedKmh:p.current_wind_speed_kmh,windGustsKmh:p.current_wind_gusts_kmh,windDirectionDeg:p.current_wind_direction_deg,weatherCode:p.current_weather_code} AS currentWeather,{waveHeightM:p.current_wave_height_m,wavePeriodS:p.current_wave_period_s,status:CASE WHEN p.current_wave_height_m IS NULL THEN 'unavailable' ELSE 'available' END} AS marineWeather,{score:p.weather_risk_score,level:p.weather_risk_level,confidence:p.weather_risk_confidence,dataCompleteness:p.weather_data_completeness,trend:p.weather_risk_trend,summary:p.weather_risk_summary,factors:w.risk_factors_json} AS risk,{max6h:w.max_risk_6h,max24h:w.max_risk_24h,average24h:w.average_risk_24h} AS forecastRisk,p.weather_updated_at AS updatedAt""",{"id":port_id})
    if not rows: raise HTTPException(404,"Port not found")
    result=rows[0]
    factors=result.get("risk",{}).get("factors")
    if isinstance(factors,str):
        try: result["risk"]["factors"]=json.loads(factors)
        except json.JSONDecodeError: result["risk"]["factors"]=[]
    return result


@app.get("/api/ports/{port_id}/weather/history", tags=["Port Weather"])
def port_weather_history(port_id: str, start: str | None=None, end: str | None=None, page: int=1, page_size: int=50) -> dict[str,Any]:
    rows=safe_query("""MATCH (p:Port)-[:HAS_WEATHER_SNAPSHOT]->(w:WeatherRiskSnapshot) WHERE coalesce(p.unlocode,p.code,p['port_id'],elementId(p))=$id AND ($start IS NULL OR w.observed_at>=datetime($start)) AND ($end IS NULL OR w.observed_at<=datetime($end)) RETURN properties(w) AS snapshot ORDER BY w.observed_at DESC SKIP $skip LIMIT $limit""",{"id":port_id,"start":start,"end":end,"skip":(max(page,1)-1)*page_size,"limit":min(max(page_size,1),200)})
    return {"page":page,"pageSize":page_size,"count":len(rows),"history":[row["snapshot"] for row in rows]}


class WeatherUpdateRequest(BaseModel):
    portIds: list[str]=[]
    force: bool=False
    dryRun: bool=False


@app.post("/api/admin/weather/update",tags=["Port Weather Admin"],status_code=202)
def trigger_weather_update(payload: WeatherUpdateRequest, background_tasks: BackgroundTasks, x_weather_admin_token: str | None=Header(None)) -> dict[str,str]:
    token=WeatherSettings().admin_token
    if not token or x_weather_admin_token != token: raise HTTPException(401,"Invalid or missing weather admin token")
    background_tasks.add_task(update_ports,payload.portIds or None,payload.force,payload.dryRun)
    return {"status":"accepted"}


@app.get("/api/risk/news", tags=["Dynamic News Risk"])
def news_risk_events(zone_id: str | None = None, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    rows = safe_query("""
        MATCH (event:NewsRiskEvent)-[:AFFECTS_ZONE]->(zone:NewsRiskZone)
        WHERE $zone_id IS NULL OR zone.zone_id=$zone_id
        RETURN event.article_id AS id,event.title AS title,event.url AS url,event.domain AS domain,
               event.seen_at AS seenAt,event.severity AS severity,event.matched_terms AS matchedTerms,
               zone.zone_id AS zoneId,zone.name AS zoneName
        ORDER BY event.seen_at DESC LIMIT $limit
    """, {"zone_id": zone_id, "limit": limit})
    return {"count": len(rows), "events": rows}


@app.get("/api/risk/news/zones", tags=["Dynamic News Risk"])
def news_risk_zones() -> dict[str, Any]:
    rows = safe_query("""
        MATCH (zone:NewsRiskZone)
        RETURN zone.zone_id AS id,zone.name AS name,zone.zone_type AS type,
               zone.current_risk_score AS riskScore,zone.current_risk_level AS riskLevel,
               zone.confidence AS confidence,zone.article_count AS articleCount,
               zone.updated_at AS updatedAt,zone.expires_at AS expiresAt,
               zone.expires_at > datetime() AS active
        ORDER BY riskScore DESC
    """)
    return {"count": len(rows), "zones": rows}


class GdeltUpdateRequest(BaseModel):
    dryRun: bool = False


@app.post("/api/admin/gdelt/update", tags=["Dynamic News Risk Admin"], status_code=202)
def trigger_gdelt_update(payload: GdeltUpdateRequest, background_tasks: BackgroundTasks, x_gdelt_admin_token: str | None = Header(None)) -> dict[str, str]:
    token = GdeltSettings().admin_token
    if not token or x_gdelt_admin_token != token:
        raise HTTPException(401, "Invalid or missing GDELT admin token")
    background_tasks.add_task(update_news_risk, payload.dryRun)
    return {"status": "accepted"}
