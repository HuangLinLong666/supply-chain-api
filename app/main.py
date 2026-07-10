"""Minimal read-only API for the AuraDB supply-chain graph."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

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
    version="0.1.0",
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
