"""Minimal read-only API for the AuraDB supply-chain graph."""

from __future__ import annotations

import os
import time
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
from pydantic import BaseModel

from app.route_optimizer import add_coordinate_fallbacks, format_route, k_shortest_paths, risk_optimization_value, shortest_path
from app.recommendation.config import load_recommendation_settings
from app.recommendation.engine import RecommendationEngine
from app.recommendation.models import RecommendationRequest, RecommendationResponse
from app.recommendation.storage import (
    GET_ROUTE_QUERY,
    GET_SNAPSHOT_QUERY,
    persist_recommendation_snapshot,
)
from database.neo4j_client import close_driver, get_settings, run_query, verify_connectivity
from weather.config import WeatherSettings
from weather.route_service import update_route_weather
from weather.service import update_ports
from gdelt.config import GdeltSettings
from gdelt.service import update_news_risk
from app.vehicle_network.api import router as vehicle_network_router
from ais.api import router as ais_router


_route_graph_cache: tuple[float, list[dict[str, Any]]] | None = None
ROUTE_GRAPH_CACHE_SECONDS = 60


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
    description="Supply-chain routing API with provider-backed weather, news, and AIS risk.",
    version="0.5.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(vehicle_network_router)
app.include_router(ais_router)


@app.middleware("http")
async def declare_json_utf8(request: Request, call_next):
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if content_type.casefold().startswith("application/json") and "charset=" not in content_type.casefold():
        response.headers["content-type"] = "application/json; charset=utf-8"
    return response


def safe_query(query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    try:
        return run_query(query, parameters)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def decoded_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


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
          CASE WHEN coalesce(p.congestion_provider,p.port_congestion_provider,'')=''
                    OR (p.congestion_provider='AISStream.io' AND
                        (p.traffic_expires_at IS NULL OR p.traffic_expires_at<=datetime()))
               THEN null ELSE p.congestionRisk END AS congestionRisk,
          CASE WHEN coalesce(p.congestion_provider,p.port_congestion_provider,'')=''
                    OR (p.congestion_provider='AISStream.io' AND
                        (p.traffic_expires_at IS NULL OR p.traffic_expires_at<=datetime()))
               THEN null ELSE p.congestion_score END AS congestion_score,
          coalesce(p.congestion_provider,p.port_congestion_provider) AS congestion_provider,
          CASE WHEN p.traffic_expires_at>datetime() THEN p.traffic_status ELSE 'unavailable' END AS traffic_status,
          p.traffic_observed_at AS traffic_observed_at,
          p.traffic_expires_at AS traffic_expires_at,
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
          CASE WHEN s.provider_risk_status IN ['available','partial']
                 AND s.provider_risk_expires_at IS NOT NULL
                 AND datetime(toString(s.provider_risk_expires_at)) > datetime()
               THEN s.provider_risk_score END AS risk_score,
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
             CASE WHEN segment.provider_risk_status IN ['available','partial']
                    AND segment.provider_risk_expires_at IS NOT NULL
                    AND datetime(toString(segment.provider_risk_expires_at)) > datetime()
                  THEN segment.provider_risk_score END AS riskScore
        WHERE riskScore IS NOT NULL AND riskScore >= $minimum_risk
        RETURN
          coalesce(segment.segmentId, segment.segment_id, elementId(segment)) AS segment_id,
          coalesce(fromNode.name, fromNode.code, fromNode.id, segment.fromNodeName) AS origin,
          coalesce(toNode.name, toNode.code, toNode.id, segment.toNodeName) AS destination,
          coalesce(segment.mode, segment.routeMode) AS mode,
          riskScore AS comprehensive_risk_score,
          segment.provider_risk_score_100 AS comprehensive_risk_score_100,
          segment.provider_risk_status AS risk_status,
          segment.provider_risk_data_completeness AS risk_data_completeness,
          properties(segment)['provider_risk_confidence'] AS risk_confidence,
          segment.provider_risk_missing_factors AS missing_factors,
          segment.provider_risk_providers AS providers,
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
          segment.costRiskScore AS cost_risk_score,
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
        RETURN elementId(node) AS node_id, name, labels(node) AS labels,
               coalesce(node.location_id,node.unlocode,node.iata,node.code,node.id) AS location_id,
               node.latitude AS lat,node.longitude AS lng,
               node.coordinate_source AS coordinate_source,
               node.coordinate_status AS coordinate_status,
               node.coordinate_confidence AS coordinate_confidence
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
        WHERE coalesce(segment.feasibility_status,'') <> 'invalid_cross_ocean'
        OPTIONAL MATCH (segment)-[spatialExposure:PASSES_THROUGH]->(geoZone:GeoZone)
        WHERE coalesce(spatialExposure.active,true)=true
        WITH segment,fromNode,toNode,
          [item IN collect(DISTINCT CASE WHEN geoZone IS NULL THEN null ELSE {
            zoneId:geoZone.zone_id,zoneName:geoZone.name,
            exposureMethod:spatialExposure.exposure_method,
            confidence:spatialExposure.confidence,
            exposureRatio:spatialExposure.exposure_ratio,
            intersectionDistanceKm:spatialExposure.intersection_distance_km
          } END) WHERE item IS NOT NULL] AS spatial_exposures
        OPTIONAL MATCH (segment)-[:HAS_COST_OBSERVATION]->(costObservation:CostObservation)
        WITH segment,fromNode,toNode,spatial_exposures,costObservation
        ORDER BY coalesce(costObservation.observed_at,costObservation.collected_at,costObservation.created_at) DESC
        WITH segment,fromNode,toNode,spatial_exposures,
             head(collect(properties(costObservation))) AS cost_observation
        OPTIONAL MATCH (segment)-[:HAS_DELAY_OBSERVATION]->(delayObservation:DelayObservation)
        WITH segment,fromNode,toNode,spatial_exposures,cost_observation,delayObservation
        ORDER BY coalesce(delayObservation.observed_at,delayObservation.collected_at,delayObservation.created_at) DESC
        WITH segment,fromNode,toNode,spatial_exposures,cost_observation,
             head(collect(properties(delayObservation))) AS delay_observation
        RETURN
          elementId(fromNode) AS from_id,
          coalesce(fromNode.name, fromNode.code, fromNode.id, segment.fromNodeName) AS from_name,
          fromNode.city AS from_city,
          fromNode.country AS from_country,
          coalesce(fromNode.location_id,fromNode.unlocode,fromNode.iata,fromNode.code,fromNode.id) AS from_location_id,
          fromNode.canonical_unlocode AS from_canonical_unlocode,
          fromNode.latitude AS from_lat,
          fromNode.longitude AS from_lng,
          fromNode.coordinate_source AS from_coordinate_source,
          fromNode.coordinate_status AS from_coordinate_status,
          fromNode.coordinate_confidence AS from_coordinate_confidence,
          labels(fromNode) AS from_labels,
          elementId(toNode) AS to_id,
          coalesce(toNode.name, toNode.code, toNode.id, segment.toNodeName) AS to_name,
          toNode.city AS to_city,
          toNode.country AS to_country,
          coalesce(toNode.location_id,toNode.unlocode,toNode.iata,toNode.code,toNode.id) AS to_location_id,
          toNode.canonical_unlocode AS to_canonical_unlocode,
          toNode.latitude AS to_lat,
          toNode.longitude AS to_lng,
          toNode.coordinate_source AS to_coordinate_source,
          toNode.coordinate_status AS to_coordinate_status,
          toNode.coordinate_confidence AS to_coordinate_confidence,
          labels(toNode) AS to_labels,
          coalesce(segment.segmentId, segment.segment_id, elementId(segment)) AS segment_id,
          coalesce(segment.canonical_mode,segment.mode,segment.routeMode) AS mode,
          coalesce(segment.mode,segment.routeMode) AS raw_mode,
          segment.data_status AS data_status,
          segment.source AS source,
          segment.source_type AS source_type,
          segment.provider AS provider,
          coalesce(segment.confidence,segment.confidence_score) AS confidence,
          segment.geometry_geojson AS geometry_geojson,
          segment.geometry_source AS geometry_source,
          segment.geometry_status AS geometry_status,
          segment.geometry_confidence AS geometry_confidence,
          segment.feasibility_status AS feasibility_status,
          spatial_exposures,
          CASE WHEN segment.provider_risk_status IN ['available','partial']
                 AND segment.provider_risk_expires_at IS NOT NULL
                 AND datetime(toString(segment.provider_risk_expires_at)) > datetime()
               THEN segment.provider_risk_score END AS risk_score,
          CASE WHEN segment.provider_risk_status IN ['available','partial']
                 AND segment.provider_risk_expires_at IS NOT NULL
                 AND datetime(toString(segment.provider_risk_expires_at)) > datetime()
               THEN segment.provider_risk_status ELSE 'unavailable' END AS risk_status,
          CASE WHEN segment.provider_risk_status IN ['available','partial']
                 AND segment.provider_risk_expires_at IS NOT NULL
                 AND datetime(toString(segment.provider_risk_expires_at)) > datetime()
               THEN coalesce(segment.provider_risk_data_completeness,0.0) ELSE 0.0 END AS risk_data_completeness,
          CASE WHEN segment.provider_risk_status IN ['available','partial']
                 AND segment.provider_risk_expires_at IS NOT NULL
                 AND datetime(toString(segment.provider_risk_expires_at)) > datetime()
               THEN properties(segment)['provider_risk_confidence'] END AS risk_confidence,
          CASE WHEN segment.provider_risk_status IN ['available','partial']
                 AND segment.provider_risk_expires_at IS NOT NULL
                 AND datetime(toString(segment.provider_risk_expires_at)) > datetime()
               THEN coalesce(segment.provider_risk_missing_factors,[]) ELSE ['expired_provider_risk'] END AS risk_missing_factors,
          CASE WHEN segment.provider_risk_status IN ['available','partial']
                 AND segment.provider_risk_expires_at IS NOT NULL
                 AND datetime(toString(segment.provider_risk_expires_at)) > datetime()
               THEN coalesce(segment.provider_risk_providers,[]) ELSE [] END AS risk_providers,
          segment.provider_risk_factors_json AS risk_factors_json,
          coalesce(segment.estimated_cost_usd, segment.baseCostUSD) AS cost_usd,
          segment.costScore AS cost_score,
          coalesce(segment.estimated_time_days, segment.estimatedTimeHours / 24.0) AS time_days,
          coalesce(segment.geometry_distance_km,segment.distance_km, segment.distanceKm) AS distance_km,
          segment.geometry_distance_km AS geometry_distance_km,
          cost_observation,
          delay_observation,
          segment.risk_explanation AS risk_explanation,
          CASE WHEN segment.provider_risk_status IN ['available','partial']
                 AND segment.provider_risk_expires_at IS NOT NULL
                 AND datetime(toString(segment.provider_risk_expires_at)) > datetime()
               THEN segment.risk_breakdown END AS risk_breakdown,
          CASE WHEN segment.news_risk_expires_at > datetime() THEN coalesce(segment.news_risk_score,0.0) ELSE 0.0 END AS news_risk_score,
          CASE WHEN segment.news_risk_expires_at > datetime() THEN segment.news_risk_zones ELSE [] END AS news_risk_zones,
          CASE WHEN segment.route_weather_expires_at > datetime() THEN segment.route_weather_risk END AS route_weather_risk,
          CASE WHEN segment.route_weather_expires_at > datetime() THEN segment.route_weather_status ELSE 'unavailable' END AS route_weather_status,
          CASE WHEN segment.route_weather_expires_at > datetime() THEN segment.route_weather_confidence END AS route_weather_confidence,
          segment.route_weather_data_completeness AS route_weather_data_completeness,
          segment.route_weather_sampling_method AS route_weather_sampling_method,
          segment.route_weather_updated_at AS route_weather_updated_at,
          segment.route_weather_expires_at AS route_weather_expires_at,
          coalesce(segment.route_weather_evidence,[]) AS route_weather_evidence,
          CASE WHEN properties(segment)['ais_congestion_expires_at'] IS NOT NULL
                    AND datetime(toString(properties(segment)['ais_congestion_expires_at'])) > datetime()
               THEN properties(segment)['ais_congestion_score'] END AS ais_congestion_score,
          CASE WHEN properties(segment)['ais_congestion_expires_at'] IS NOT NULL
                    AND datetime(toString(properties(segment)['ais_congestion_expires_at'])) > datetime()
               THEN properties(segment)['ais_congestion_status'] ELSE 'unavailable' END AS ais_congestion_status,
          CASE WHEN properties(segment)['ais_congestion_expires_at'] IS NOT NULL
                    AND datetime(toString(properties(segment)['ais_congestion_expires_at'])) > datetime()
               THEN properties(segment)['ais_congestion_confidence'] END AS ais_congestion_confidence,
          CASE WHEN properties(segment)['ais_congestion_expires_at'] IS NOT NULL
                    AND datetime(toString(properties(segment)['ais_congestion_expires_at'])) > datetime()
               THEN properties(segment)['ais_congestion_data_completeness'] ELSE 0.0 END AS ais_congestion_data_completeness,
          CASE WHEN properties(segment)['ais_congestion_expires_at'] IS NOT NULL
                    AND datetime(toString(properties(segment)['ais_congestion_expires_at'])) > datetime()
               THEN coalesce(properties(segment)['ais_congestion_snapshot_ids'],[]) ELSE [] END AS ais_congestion_evidence,
          properties(segment)['ais_congestion_observed_at'] AS ais_congestion_observed_at,
          properties(segment)['ais_congestion_expires_at'] AS ais_congestion_expires_at
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
                str(segment.get(f"{prefix}_location_id") or ""),
                str(segment.get(f"{prefix}_canonical_unlocode") or ""),
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


def recommendation_supplier(value: str) -> dict[str, Any] | None:
    rows = safe_query(
        """
        MATCH (supplier:Supplier)
        WHERE toLower(coalesce(supplier.supplier_id,supplier.supplierCode,''))=toLower($value)
           OR toLower(coalesce(supplier.name,''))=toLower($value)
           OR toLower(coalesce(supplier.supplier_id,supplier.supplierCode,'')) CONTAINS toLower($value)
           OR toLower(coalesce(supplier.name,'')) CONTAINS toLower($value)
        OPTIONAL MATCH (supplier)-[:SHIPS_FROM]->(shippingOrigin)
        WITH supplier,
             CASE WHEN toLower(coalesce(supplier.supplier_id,supplier.supplierCode,''))=toLower($value)
                        OR toLower(coalesce(supplier.name,''))=toLower($value)
                  THEN 0 ELSE 1 END AS exactRank,
             [item IN collect(DISTINCT CASE WHEN shippingOrigin IS NULL THEN null ELSE {
               elementId:elementId(shippingOrigin),
               id:coalesce(shippingOrigin.location_id,shippingOrigin.unlocode,shippingOrigin.iata,
                           shippingOrigin.code,shippingOrigin.id,shippingOrigin.name),
               name:shippingOrigin.name,
               city:shippingOrigin.city,
               country:shippingOrigin.country,
               labels:labels(shippingOrigin)
             } END) WHERE item IS NOT NULL] AS shippingOrigins
        RETURN coalesce(supplier.supplier_id,supplier.supplierCode,elementId(supplier)) AS id,
               supplier.name AS name,supplier.city AS city,supplier.country AS country,
               CASE WHEN supplier.provider_risk_status IN ['available','partial']
                          AND size(coalesce(supplier.provider_risk_providers,[]))>0
                    THEN supplier.provider_risk_score END AS riskScore,
               CASE WHEN supplier.provider_risk_status IN ['available','partial']
                          AND size(coalesce(supplier.provider_risk_providers,[]))>0
                    THEN supplier.provider_risk_status ELSE 'unavailable' END AS riskStatus,
               CASE WHEN supplier.provider_risk_status IN ['available','partial']
                          AND size(coalesce(supplier.provider_risk_providers,[]))>0
                    THEN coalesce(supplier.provider_risk_data_completeness,0.0) ELSE 0.0 END AS riskDataCompleteness,
               CASE WHEN supplier.provider_risk_status IN ['available','partial']
                          AND size(coalesce(supplier.provider_risk_providers,[]))>0
                    THEN coalesce(supplier.provider_risk_providers,[]) ELSE [] END AS riskProviders,
               CASE WHEN supplier.provider_risk_status IN ['available','partial']
                          AND size(coalesce(supplier.provider_risk_providers,[]))>0
                    THEN coalesce(supplier.provider_risk_evidence,[]) ELSE [] END AS riskEvidence,
               supplier.risk_explanation AS riskExplanation,
               shippingOrigins,
               exactRank
        ORDER BY exactRank,name
        LIMIT 1
        """,
        {"value": value},
    )
    return rows[0] if rows else None


def supplier_origin_node_ids(
    segments: list[dict[str, Any]],
    matched_origin_ids: set[str],
    supplier: dict[str, Any],
) -> set[str]:
    supplier_origins = supplier.get("shippingOrigins") or []
    if not supplier_origins:
        return set()

    def normalized(values: set[Any]) -> set[str]:
        return {str(item).strip().casefold() for item in values if item is not None and str(item).strip()}

    supplier_values = [
        normalized(
            {
                origin.get("elementId"),
                origin.get("id"),
                origin.get("name"),
                origin.get("city"),
            }
        )
        for origin in supplier_origins
    ]
    compatible: set[str] = set()
    for segment in segments:
        for prefix in ("from", "to"):
            node_id = str(segment[f"{prefix}_id"])
            if node_id not in matched_origin_ids:
                continue
            node_values = normalized(
                {
                    node_id,
                    segment.get(f"{prefix}_location_id"),
                    segment.get(f"{prefix}_canonical_unlocode"),
                    segment.get(f"{prefix}_name"),
                    segment.get(f"{prefix}_city"),
                }
            )
            if any(node_values.intersection(origin_values) for origin_values in supplier_values):
                compatible.add(node_id)
    return compatible


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
	          CASE WHEN supplier.provider_risk_status IN ['available','partial']
	               THEN supplier.provider_risk_score END AS riskScore,
	          coalesce(supplier.provider_risk_status,'unavailable') AS riskStatus,
	          coalesce(supplier.provider_risk_data_completeness,0.0) AS riskDataCompleteness,
	          coalesce(supplier.provider_risk_missing_factors,[]) AS riskMissingFactors,
	          coalesce(supplier.provider_risk_providers,[]) AS riskProviders,
          supplier.risk_explanation AS riskExplanation,
          routeCount AS routeCount
        ORDER BY routeCount DESC, name
        LIMIT $limit
        """,
        {"search": search, "limit": limit},
    )
    return {"count": len(rows), "suppliers": rows}


@app.get(
    "/api/suppliers/{supplier_id}/origins",
    tags=["Route Planning"],
    summary="List origins explicitly linked to a supplier",
)
def supplier_origins(supplier_id: str) -> dict[str, Any]:
    supplier = recommendation_supplier(supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id!r} was not found")
    origins = supplier.pop("shippingOrigins", [])
    supplier.pop("exactRank", None)
    return {"supplier": supplier, "count": len(origins), "origins": origins}


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
               node.latitude AS lat, node.longitude AS lng, labels(node) AS labels,
               coalesce(node.location_id,node.unlocode,node.iata,node.code,node.id) AS locationId,
               node.canonical_unlocode AS canonicalUnlocode,
               node.coordinate_source AS coordinateSource,
               node.coordinate_source_url AS coordinateSourceUrl,
               node.coordinate_status AS coordinateStatus,
               node.coordinate_confidence AS coordinateConfidence,
               node.coordinate_collected_at AS coordinateCollectedAt
        ORDER BY value, name
        LIMIT $limit
        """,
        {"search": search, "limit": limit},
    )
    return {"count": len(rows), "cities": rows}


@app.get(
    "/api/geography/locations",
    tags=["Geospatial"],
    summary="List route locations with coordinate provenance",
)
def geography_locations(
    status: str | None = Query(None, description="Filter by coordinate_status"),
    limit: int = Query(250, ge=1, le=1000),
) -> dict[str, Any]:
    status_value = status if isinstance(status, str) else None
    rows = safe_query(
        """
        MATCH (location:TransportLocation)
        WHERE $status IS NULL OR location.coordinate_status=$status
        RETURN coalesce(location.location_id,location.unlocode,location.iata,location.code,location.id) AS locationId,
               coalesce(location.name_zh,location.name_en,location.name) AS name,
               location.city AS city,location.country AS country,labels(location) AS labels,
               location.unlocode AS legacyUnlocode,
               location.canonical_unlocode AS canonicalUnlocode,
               location.identity_status AS identityStatus,
               location.latitude AS lat,location.longitude AS lng,
               location.coordinate_source AS coordinateSource,
               location.coordinate_source_url AS coordinateSourceUrl,
               location.coordinate_license AS coordinateLicense,
               location.coordinate_status AS coordinateStatus,
               location.coordinate_confidence AS coordinateConfidence,
               location.coordinate_collected_at AS coordinateCollectedAt
        ORDER BY locationId
        LIMIT $limit
        """,
        {"status": status_value, "limit": limit},
    )
    return {"count": len(rows), "locations": rows}


@app.get(
    "/api/geography/zones",
    tags=["Geospatial"],
    summary="List geospatial risk zones and their source metadata",
)
def geography_zones(include_geometry: bool = Query(True)) -> dict[str, Any]:
    rows = safe_query(
        """
        MATCH (zone:GeoZone)
        RETURN zone.zone_id AS zoneId,zone.name AS name,zone.zone_type AS zoneType,
               zone.applicable_modes AS applicableModes,
               zone.geometry_geojson AS geometry,
               zone.geometry_source AS geometrySource,
               zone.geometry_source_url AS geometrySourceUrl,
               zone.geometry_license AS geometryLicense,
               zone.geometry_status AS geometryStatus,
               zone.geometry_confidence AS geometryConfidence,
               zone.geometry_collected_at AS geometryCollectedAt,
               zone.current_risk_score AS currentRiskScore,
               zone.current_risk_level AS currentRiskLevel,
               zone.updated_at AS riskUpdatedAt,zone.expires_at AS riskExpiresAt
        ORDER BY zoneId
        """
    )
    for row in rows:
        if include_geometry:
            row["geometry"] = decoded_json(row.get("geometry"))
        else:
            row.pop("geometry", None)
    return {"count": len(rows), "zones": rows}


@app.get(
    "/api/geography/segments/{segment_id}",
    tags=["Geospatial"],
    summary="Get one route geometry and its risk-zone intersections",
)
def geography_segment(segment_id: str) -> dict[str, Any]:
    rows = safe_query(
        """
        MATCH (segment:RouteSegment)
        WHERE coalesce(segment.segment_id,segment.segmentId,elementId(segment))=$segment_id
        OPTIONAL MATCH (segment)-[:FROM_NODE]->(origin)
        OPTIONAL MATCH (segment)-[:TO_NODE]->(destination)
        OPTIONAL MATCH (segment)-[exposure:PASSES_THROUGH]->(zone:GeoZone)
        WHERE coalesce(exposure.active,true)=true
        WITH segment,head(collect(DISTINCT origin)) AS origin,
             head(collect(DISTINCT destination)) AS destination,
             [item IN collect(DISTINCT CASE WHEN zone IS NULL THEN null ELSE {
               zoneId:zone.zone_id,zoneName:zone.name,
               exposureMethod:exposure.exposure_method,
               intersectionDistanceKm:exposure.intersection_distance_km,
               routeDistanceKm:exposure.route_distance_km,
               exposureRatio:exposure.exposure_ratio,
               confidence:exposure.confidence,
               geometryStatus:exposure.geometry_status
             } END) WHERE item IS NOT NULL] AS exposures
        RETURN coalesce(segment.segment_id,segment.segmentId,elementId(segment)) AS segmentId,
               coalesce(segment.canonical_mode,segment.mode,segment.routeMode) AS mode,
               segment.data_status AS dataStatus,
               segment.feasibility_status AS feasibilityStatus,
               segment.feasibility_reason AS feasibilityReason,
               segment.geometry_geojson AS geometry,
               segment.geometry_source AS geometrySource,
               segment.geometry_source_url AS geometrySourceUrl,
               segment.geometry_license AS geometryLicense,
               segment.geometry_status AS geometryStatus,
               segment.geometry_confidence AS geometryConfidence,
               segment.geometry_distance_km AS geometryDistanceKm,
               segment.geometry_method AS geometryMethod,
               segment.geometry_generated_at AS geometryGeneratedAt,
               {id:coalesce(origin.location_id,origin.unlocode,origin.iata,origin.code,origin.id),
                name:coalesce(origin.name_zh,origin.name_en,origin.name),city:origin.city,country:origin.country,
                lat:origin.latitude,lng:origin.longitude,coordinateSource:origin.coordinate_source,
                coordinateStatus:origin.coordinate_status,coordinateConfidence:origin.coordinate_confidence} AS origin,
               {id:coalesce(destination.location_id,destination.unlocode,destination.iata,destination.code,destination.id),
                name:coalesce(destination.name_zh,destination.name_en,destination.name),city:destination.city,country:destination.country,
                lat:destination.latitude,lng:destination.longitude,coordinateSource:destination.coordinate_source,
                coordinateStatus:destination.coordinate_status,coordinateConfidence:destination.coordinate_confidence} AS destination,
               exposures
        """,
        {"segment_id": segment_id},
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"RouteSegment {segment_id!r} was not found")
    result = rows[0]
    result["geometry"] = decoded_json(result.get("geometry"))
    return result


@app.post(
    "/api/routes/recommend",
    tags=["Route Planning"],
    summary="Recommend routes with multi-objective weights and hard constraints",
    response_model=RecommendationResponse,
    response_model_by_alias=True,
)
def recommend_routes_post(payload: RecommendationRequest) -> RecommendationResponse:
    supplier = recommendation_supplier(payload.supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail=f"Supplier {payload.supplier_id!r} was not found")

    segments = route_graph_segments()
    matched_origin_ids = matching_node_ids(segments, payload.origin)
    destination_ids = matching_node_ids(segments, payload.destination)
    if not matched_origin_ids:
        raise HTTPException(status_code=404, detail=f"Origin {payload.origin!r} was not found in the route network")
    if not destination_ids:
        raise HTTPException(status_code=404, detail=f"Destination {payload.destination!r} was not found in the route network")
    if not supplier.get("shippingOrigins"):
        raise HTTPException(
            status_code=422,
            detail=f"Supplier {supplier['id']!r} has no SHIPS_FROM origin mapping; recommendation was not guessed",
        )
    origin_ids = supplier_origin_node_ids(segments, matched_origin_ids, supplier)
    if not origin_ids:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Origin {payload.origin!r} is not linked to supplier {supplier['id']!r}; "
                "use GET /api/suppliers/{supplier_id}/origins"
            ),
        )

    engine = RecommendationEngine()
    try:
        result = engine.recommend(segments, origin_ids, destination_ids, supplier, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result["networkPathFound"]:
        raise HTTPException(
            status_code=404,
            detail="No directed feasible RouteSegment path connects the supplier origin and destination",
        )

    generated_at = datetime.now(timezone.utc)
    supplier_summary = {
        "id": supplier["id"],
        "name": supplier.get("name"),
        "city": supplier.get("city"),
        "country": supplier.get("country"),
    }
    response = RecommendationResponse.model_validate(
        {
            "snapshotId": f"recommendation_{uuid4().hex}",
            "scoringVersion": engine.scoring_version,
            "generatedAt": generated_at,
            "query": {
                "supplier": supplier_summary,
                "origin": payload.origin,
                "destination": payload.destination,
                "resolvedOriginNodeIds": sorted(origin_ids),
                "resolvedDestinationNodeIds": sorted(destination_ids),
                "cargo": payload.cargo.model_dump(mode="json", by_alias=True),
                "strategy": payload.strategy.value,
                "constraintsAppliedBeforeRanking": True,
            },
            "resolvedWeights": engine.resolved_weights(payload).model_dump(mode="json", by_alias=True),
            "normalization": engine.normalization_metadata(),
            "dynamicRouting": result["dynamicRouting"],
            "candidateCount": result["candidateCount"],
            "eligibleCount": result["eligibleCount"],
            "count": len(result["routes"]),
            "rejectedCandidates": result["rejectedCandidates"],
            "routes": result["routes"],
        }
    )
    settings = load_recommendation_settings()
    persist_recommendation_snapshot(
        safe_query,
        payload,
        response,
        result["includedSegments"],
        int(settings["snapshot"]["retention_days"]),
    )
    return response


@app.get(
    "/api/routes/recommend",
    tags=["Route Planning"],
    summary="Query multiple complete routes by supplier, origin, and destination",
    deprecated=True,
    description="兼容旧前端；新开发请使用同路径的 POST 接口。",
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
    supplier_risk = exact_supplier.get("riskScore")
    supplier_risk = float(supplier_risk) if supplier_risk is not None else None
    supplier_completeness = float(exact_supplier.get("riskDataCompleteness") or 0.0)
    for route in formatted:
        route_risk = route.get("riskScore")
        if supplier_risk is not None and route_risk is not None:
            route["riskScore"] = round(0.2 * supplier_risk * 100 + 0.8 * float(route_risk))
            route["riskDataCompleteness"] = round(
                0.2 * supplier_completeness + 0.8 * float(route.get("riskDataCompleteness") or 0.0),
                4,
            )
            route["riskStatus"] = (
                "available"
                if exact_supplier.get("riskStatus") == "available" and route.get("riskStatus") == "available"
                else "partial"
            )
            route["riskProviders"] = sorted(
                set(route.get("riskProviders") or []) | set(exact_supplier.get("riskProviders") or [])
            )
        elif supplier_risk is None:
            route["riskStatus"] = "unavailable" if route_risk is None else "partial"
            route["riskMissingFactors"] = sorted(
                set(route.get("riskMissingFactors") or []) | {"supplier_risk"}
            )
        route["riskFactors"].insert(
            0,
            {
                "key": "supplier",
                "label": "供应商",
                "score": round(supplier_risk * 100) if supplier_risk is not None else None,
                "status": exact_supplier.get("riskStatus") or "unavailable",
                "provider": (exact_supplier.get("riskProviders") or [None])[0],
                "detail": exact_supplier.get("riskExplanation") or f"供应商 {exact_supplier['name']} 暂无可验证风险 Provider",
            },
        )
    legacy_engine = RecommendationEngine()
    formatted.sort(
        key=lambda route: risk_weight
        * risk_optimization_value(
            {
                "risk_score": route["riskScore"] / 100 if route.get("riskScore") is not None else None,
                "risk_data_completeness": route.get("riskDataCompleteness"),
            }
        )
        + (1 - risk_weight) * legacy_engine.penalty_score(float(route["cost"]), "cost_per_vehicle_usd")
    )
    routes = formatted[:limit]
    if routes:
        min(routes, key=lambda route: route["cost"])["tags"].insert(0, "成本最优")
        known_risk_routes = [route for route in routes if route.get("riskScore") is not None]
        if known_risk_routes:
            min(known_risk_routes, key=lambda route: route["riskScore"])["tags"].insert(0, "风险最优")
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
             CASE WHEN segment.provider_risk_status IN ['available','partial']
                    AND segment.provider_risk_expires_at IS NOT NULL
                    AND datetime(toString(segment.provider_risk_expires_at)) > datetime()
                  THEN segment.provider_risk_score END AS risk,
             CASE WHEN segment.provider_risk_status IN ['available','partial']
                    AND segment.provider_risk_expires_at IS NOT NULL
                    AND datetime(toString(segment.provider_risk_expires_at)) > datetime()
                  THEN coalesce(segment.provider_risk_data_completeness,0.0) ELSE 0.0 END AS riskCompleteness,
             CASE WHEN segment.provider_risk_status IN ['available','partial']
                    AND segment.provider_risk_expires_at IS NOT NULL
                    AND datetime(toString(segment.provider_risk_expires_at)) > datetime()
                  THEN segment.provider_risk_status ELSE 'unavailable' END AS riskStatus,
             CASE WHEN segment.provider_risk_status IN ['available','partial']
                    AND segment.provider_risk_expires_at IS NOT NULL
                    AND datetime(toString(segment.provider_risk_expires_at)) > datetime()
                  THEN coalesce(segment.provider_risk_missing_factors,[]) ELSE ['expired_provider_risk'] END AS missingFactors,
             coalesce(segment.estimated_cost_usd, segment.baseCostUSD, 0.0) AS cost,
             coalesce(segment.costScore, 0.5) AS costScore
        WITH route,segment,membership,risk,riskCompleteness,riskStatus,missingFactors,cost,costScore,
             CASE WHEN risk IS NULL THEN 1.0
                  ELSE risk + (1.0-riskCompleteness)*0.25 END AS riskPenalty
        ORDER BY coalesce(membership.sequence, segment.sequence, 0)
        WITH route,
             collect({
               segment_id: coalesce(segment.segmentId, segment.segment_id, elementId(segment)),
               sequence: coalesce(membership.sequence, segment.sequence),
               origin: segment.fromNodeName,
               destination: segment.toNodeName,
               mode: coalesce(segment.mode, segment.routeMode),
               risk_score: risk,
               risk_status: riskStatus,
               risk_data_completeness: riskCompleteness,
               missing_factors: missingFactors,
               estimated_cost_usd: cost
             }) AS segments,
             sum(cost) AS totalCost,
             avg(risk) AS averageRisk,
             max(risk) AS maximumRisk,
             avg(riskPenalty) AS averageRiskPenalty,
             avg(riskCompleteness) AS riskDataCompleteness,
             count(risk) AS knownRiskSegments,
             count(segment) AS segmentCount,
             avg(costScore) AS averageCostScore,
             sum(coalesce(segment.estimated_time_days, segment.estimatedTimeHours / 24.0, 0.0)) AS totalTime
        WITH route, segments, totalCost, averageRisk, maximumRisk,averageRiskPenalty,
             riskDataCompleteness,knownRiskSegments,segmentCount,averageCostScore,totalTime,
             CASE $objective
               WHEN 'min_cost' THEN totalCost
               WHEN 'min_risk' THEN averageRiskPenalty
               ELSE $risk_weight * averageRiskPenalty + (1.0 - $risk_weight) * averageCostScore
             END AS optimizationScore
        RETURN
          coalesce(route.route_id, route.routeId, elementId(route)) AS route_id,
          route.name AS name,
          optimizationScore AS optimization_score,
          totalCost AS total_cost_usd,
          averageRisk AS average_risk_score,
          maximumRisk AS maximum_risk_score,
          CASE WHEN knownRiskSegments=0 THEN 'unavailable'
               WHEN knownRiskSegments=segmentCount AND riskDataCompleteness>=0.9999 THEN 'available'
               ELSE 'partial' END AS risk_status,
          riskDataCompleteness AS risk_data_completeness,
          knownRiskSegments AS risk_known_segments,
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


@app.get("/api/routes/weather-risks", tags=["Route Weather"])
def route_weather_risks(
    mode: Literal["sea", "air", "rail", "road"] | None = None,
    status: Literal["available", "partial", "unavailable"] | None = None,
    active_only: bool = True,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    rows = safe_query(
        """
        MATCH (segment:RouteSegment)-[:FROM_NODE]->(origin)
        MATCH (segment)-[:TO_NODE]->(destination)
        WHERE segment.route_weather_provider='Open-Meteo'
          AND ($mode IS NULL OR toLower(toString(coalesce(segment.canonical_mode,segment.mode,segment.routeMode)))=$mode)
          AND ($status IS NULL OR coalesce(segment.route_weather_status,'unavailable')=$status)
          AND (NOT $active_only OR segment.route_weather_expires_at > datetime())
        RETURN coalesce(segment.segment_id,segment.segmentId,elementId(segment)) AS segmentId,
               coalesce(origin.name,origin.code,origin.id) AS origin,
               coalesce(destination.name,destination.code,destination.id) AS destination,
               toLower(toString(coalesce(segment.canonical_mode,segment.mode,segment.routeMode))) AS mode,
               segment.route_weather_risk AS score,segment.route_weather_level AS level,
               segment.route_weather_status AS status,
               segment.route_weather_confidence AS confidence,
               segment.route_weather_data_completeness AS dataCompleteness,
               segment.route_weather_sampling_method AS samplingMethod,
               segment.route_weather_geometry_status AS geometryStatus,
               segment.route_weather_sample_count AS sampleCount,
               segment.route_weather_valid_sample_count AS validSampleCount,
               segment.route_weather_evidence AS evidence,
               segment.route_weather_updated_at AS updatedAt,
               segment.route_weather_expires_at AS expiresAt,
               segment.route_weather_expires_at > datetime() AS active
        ORDER BY active DESC,score DESC
        LIMIT $limit
        """,
        {"mode": mode, "status": status, "active_only": active_only, "limit": limit},
    )
    return {"count": len(rows), "routes": rows}


@app.get("/api/routes/weather-risks/{segment_id}", tags=["Route Weather"])
def route_weather_risk_detail(segment_id: str) -> dict[str, Any]:
    rows = safe_query(
        """
        MATCH (segment:RouteSegment)
        WHERE coalesce(segment.segment_id,segment.segmentId,elementId(segment))=$segment_id
        OPTIONAL MATCH (segment)-[:HAS_ROUTE_WEATHER_SNAPSHOT]->(snapshot:RouteWeatherRiskSnapshot)
        WITH segment,snapshot ORDER BY snapshot.observed_at DESC
        WITH segment,head(collect(snapshot)) AS snapshot
        RETURN coalesce(segment.segment_id,segment.segmentId,elementId(segment)) AS segmentId,
               properties(segment) AS segmentWeather,
               properties(snapshot) AS latestSnapshot
        """,
        {"segment_id": segment_id},
    )
    if not rows:
        raise HTTPException(404, "Route segment not found")
    result = rows[0]
    snapshot = result.get("latestSnapshot") or {}
    for field in ("samples_json", "risk_factors_json"):
        value = snapshot.pop(field, None)
        if isinstance(value, str):
            try:
                snapshot[field.removesuffix("_json")] = json.loads(value)
            except json.JSONDecodeError:
                snapshot[field.removesuffix("_json")] = []
    result["latestSnapshot"] = snapshot or None
    return result


class RouteWeatherUpdateRequest(BaseModel):
    segmentIds: list[str] = []
    limit: int | None = None
    dryRun: bool = False


@app.post("/api/admin/weather/routes/update", tags=["Route Weather Admin"], status_code=202)
def trigger_route_weather_update(
    payload: RouteWeatherUpdateRequest,
    background_tasks: BackgroundTasks,
    x_weather_admin_token: str | None = Header(None),
) -> dict[str, str]:
    token = WeatherSettings().admin_token
    if not token or x_weather_admin_token != token:
        raise HTTPException(401, "Invalid or missing weather admin token")
    background_tasks.add_task(
        update_route_weather,
        payload.segmentIds or None,
        limit=payload.limit,
        dry_run=payload.dryRun,
    )
    return {"status": "accepted"}


@app.get("/api/risk/news", tags=["Dynamic News Risk"])
def news_risk_events(zone_id: str | None = None, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    rows = safe_query("""
        MATCH (event:NewsRiskEvent)-[:AFFECTS_ZONE]->(zone:NewsRiskZone)
        WHERE $zone_id IS NULL OR zone.zone_id=$zone_id
        OPTIONAL MATCH (event)-[:MEMBER_OF_EVENT_CLUSTER]->(cluster:NewsRiskCluster)-[:AFFECTS_ZONE]->(zone)
        WITH event,zone,head(collect(DISTINCT cluster)) AS cluster
        RETURN event.article_id AS id,event.title AS title,event.url AS url,event.domain AS domain,
               event.seen_at AS seenAt,event.severity AS severity,event.matched_terms AS matchedTerms,
               event.canonical_url AS canonicalUrl,event.event_category AS category,
               event.matched_categories AS matchedCategories,
               event.classification_status AS classificationStatus,event.time_status AS timeStatus,
               event.source_credibility_status AS sourceCredibilityStatus,
               cluster.cluster_id AS clusterId,cluster.article_count AS clusterArticleCount,
               cluster.distinct_domain_count AS clusterSourceCount,
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
               zone.status AS status,zone.confidence AS confidence,zone.article_count AS articleCount,
               zone.raw_article_count AS rawArticleCount,zone.valid_article_count AS validArticleCount,
               zone.event_cluster_count AS clusterCount,zone.category_counts_json AS categoryCounts,
               zone.rejected_counts_json AS rejectedCounts,
               zone.source_credibility_status AS sourceCredibilityStatus,
               zone.updated_at AS updatedAt,zone.expires_at AS expiresAt,
               zone.expires_at > datetime() AS active
        ORDER BY riskScore DESC
    """)
    for row in rows:
        for field in ("categoryCounts", "rejectedCounts"):
            if isinstance(row.get(field), str):
                try:
                    row[field] = json.loads(row[field])
                except json.JSONDecodeError:
                    row[field] = {}
    return {"count": len(rows), "zones": rows}


@app.get("/api/risk/news/clusters", tags=["Dynamic News Risk"])
def news_risk_clusters(
    zone_id: str | None = None,
    category: str | None = None,
    active_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    rows = safe_query(
        """
        MATCH (cluster:NewsRiskCluster)-[:AFFECTS_ZONE]->(zone:NewsRiskZone)
        WHERE ($zone_id IS NULL OR zone.zone_id=$zone_id)
          AND ($category IS NULL OR cluster.event_category=$category)
          AND (NOT $active_only OR cluster.expires_at > datetime())
        RETURN cluster.cluster_id AS id,cluster.event_category AS category,
               cluster.representative_title AS title,cluster.severity AS severity,
               cluster.effective_severity AS effectiveSeverity,
               cluster.article_count AS articleCount,
               cluster.distinct_domain_count AS sourceCount,cluster.domains AS domains,
               cluster.first_seen AS firstSeen,cluster.last_seen AS lastSeen,
               cluster.source_credibility_status AS sourceCredibilityStatus,
               cluster.expires_at AS expiresAt,
               coalesce(cluster.expires_at > datetime(),false) AS active,
               zone.zone_id AS zoneId,zone.name AS zoneName
        ORDER BY lastSeen DESC,effectiveSeverity DESC
        LIMIT $limit
        """,
        {"zone_id": zone_id, "category": category, "active_only": active_only, "limit": limit},
    )
    return {"count": len(rows), "clusters": rows}


class GdeltUpdateRequest(BaseModel):
    dryRun: bool = False
    zoneIds: list[str] = []


@app.post("/api/admin/gdelt/update", tags=["Dynamic News Risk Admin"], status_code=202)
def trigger_gdelt_update(payload: GdeltUpdateRequest, background_tasks: BackgroundTasks, x_gdelt_admin_token: str | None = Header(None)) -> dict[str, str]:
    token = GdeltSettings().admin_token
    if not token or x_gdelt_admin_token != token:
        raise HTTPException(401, "Invalid or missing GDELT admin token")
    background_tasks.add_task(update_news_risk, payload.dryRun, zone_ids=payload.zoneIds or None)
    return {"status": "accepted"}


@app.get(
    "/api/recommendations/{snapshot_id}",
    tags=["Route Planning"],
    summary="Read a persisted recommendation snapshot",
    response_model=RecommendationResponse,
    response_model_by_alias=True,
)
def recommendation_snapshot(snapshot_id: str) -> RecommendationResponse:
    rows = safe_query(GET_SNAPSHOT_QUERY, {"snapshot_id": snapshot_id})
    if not rows:
        raise HTTPException(status_code=404, detail=f"RecommendationSnapshot {snapshot_id!r} was not found")
    payload = decoded_json(rows[0].get("response_json"))
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="RecommendationSnapshot response_json is invalid")
    return RecommendationResponse.model_validate(payload)


@app.get(
    "/api/routes/{route_id}",
    tags=["Route Planning"],
    summary="Read one route from its latest recommendation snapshot",
)
def recommended_route_detail(route_id: str) -> dict[str, Any]:
    rows = safe_query(GET_ROUTE_QUERY, {"route_id": route_id})
    if not rows:
        raise HTTPException(status_code=404, detail=f"Recommended route {route_id!r} was not found")
    payload = decoded_json(rows[0].get("response_json"))
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="RecommendationSnapshot response_json is invalid")
    route = next((item for item in payload.get("routes", []) if item.get("id") == route_id), None)
    if route is None:
        raise HTTPException(status_code=404, detail=f"Recommended route {route_id!r} was not found in snapshot")
    return {
        "snapshotId": rows[0]["snapshot_id"],
        "createdAt": rows[0].get("created_at"),
        "route": route,
    }
