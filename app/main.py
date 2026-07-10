"""Minimal read-only API for the AuraDB supply-chain graph."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.route_optimizer import shortest_path
from database.neo4j_client import close_driver, get_settings, run_query, verify_connectivity


def cors_origins() -> list[str]:
    raw = os.getenv("API_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
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
    allow_methods=["GET"],
    allow_headers=["*"],
)


def safe_query(query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    try:
        return run_query(query, parameters)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
    return safe_query(
        """
        MATCH (segment:RouteSegment)-[:FROM_NODE]->(fromNode)
        MATCH (segment)-[:TO_NODE]->(toNode)
        RETURN
          elementId(fromNode) AS from_id,
          coalesce(fromNode.name, fromNode.code, fromNode.id, segment.fromNodeName) AS from_name,
          labels(fromNode) AS from_labels,
          elementId(toNode) AS to_id,
          coalesce(toNode.name, toNode.code, toNode.id, segment.toNodeName) AS to_name,
          labels(toNode) AS to_labels,
          coalesce(segment.segmentId, segment.segment_id, elementId(segment)) AS segment_id,
          coalesce(segment.mode, segment.routeMode) AS mode,
          coalesce(segment.total_risk_score, segment.riskScore, segment.base_risk_score, 0.5) AS risk_score,
          coalesce(segment.estimated_cost_usd, segment.baseCostUSD, 0.0) AS cost_usd,
          coalesce(segment.costScore, 0.5) AS cost_score,
          coalesce(segment.costRiskScore, 0.5) AS cost_risk_score,
          coalesce(segment.estimated_time_days, segment.estimatedTimeHours / 24.0, 0.0) AS time_days,
          segment.risk_explanation AS risk_explanation
        """
    )


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
) -> dict[str, Any]:
    if origin_id == destination_id:
        raise HTTPException(status_code=400, detail="origin_id and destination_id must be different")
    result = shortest_path(route_graph_segments(), origin_id, destination_id, objective, risk_weight)
    if result is None:
        raise HTTPException(status_code=404, detail="No directed RouteSegment path connects the selected nodes")
    result["origin_id"] = origin_id
    result["destination_id"] = destination_id
    result["risk_weight"] = risk_weight if objective == "balanced" else None
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
