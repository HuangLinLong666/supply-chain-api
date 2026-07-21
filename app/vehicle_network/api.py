from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Query

from app.vehicle_network.core import load_strategy, save_strategy
from app.vehicle_network.models import AuditSourceRequest, LocationIngestRequest, ReviewRequest, RouteGenerateRequest, StrategyConfig
from app.vehicle_network.repository import VehicleNetworkRepository
from app.vehicle_network.services import LocationIngestionService, RouteGenerationService
from database.neo4j_client import verify_connectivity


router = APIRouter(prefix="/api/v1", tags=["整车运输路径网络"])


def trace_id(value: str | None) -> str:
    return value or f"trace_{uuid4().hex}"


@router.post("/locations/ingest")
async def ingest_locations(payload: LocationIngestRequest, x_trace_id: str | None = Header(None)) -> dict[str, Any]:
    try:
        return await LocationIngestionService().ingest(payload, trace_id(x_trace_id))
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/routes/generate")
def generate_routes(payload: RouteGenerateRequest, x_trace_id: str | None = Header(None)) -> dict[str, Any]:
    try:
        return RouteGenerationService().generate(payload, trace_id(x_trace_id))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/routes/search")
def search_routes(origin: str, destination: str, ranking_strategy: str = "hybrid", limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    rows = VehicleNetworkRepository().search_routes(origin, destination, limit)
    return {"success": True, "query": {"origin": origin, "destination": destination, "ranking_strategy": ranking_strategy}, "count": len(rows), "routes": rows}


@router.get("/routes/{route_id}")
def route_detail(route_id: str) -> dict[str, Any]:
    route = VehicleNetworkRepository().get_route(route_id)
    if not route:
        raise HTTPException(404, "路线不存在")
    return {"success": True, **route}


@router.post("/routes/{route_id}/score/recompute")
def recompute_route_score(route_id: str, x_trace_id: str | None = Header(None)) -> dict[str, Any]:
    try:
        return RouteGenerationService().recompute(route_id, trace_id(x_trace_id))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/routes/{route_id}/review")
def review_route(route_id: str, payload: ReviewRequest) -> dict[str, Any]:
    if not VehicleNetworkRepository().review_route(route_id, payload.review_status, payload.reviewed_by, payload.note):
        raise HTTPException(404, "路线不存在")
    return {"success": True, "route_id": route_id, "review_status": payload.review_status}


@router.delete("/routes/{route_id}")
def delete_route(route_id: str, x_actor: str = Header("api_user")) -> dict[str, Any]:
    if not VehicleNetworkRepository().soft_delete_route(route_id, x_actor):
        raise HTTPException(404, "路线不存在")
    return {"success": True, "route_id": route_id, "deleted": "soft"}


@router.post("/audit/source")
def audit_source(payload: AuditSourceRequest) -> dict[str, Any]:
    evidence_id = VehicleNetworkRepository().add_source_audit(payload)
    return {"success": True, "evidence_id": evidence_id}


@router.get("/health")
def vehicle_network_health() -> dict[str, Any]:
    try:
        verify_connectivity()
        database = "connected"
    except Exception as exc:
        database = f"error: {exc}"
    return {"status": "ok" if database == "connected" else "degraded", "neo4j": database, "module": "vehicle_transport_network"}


@router.get("/config/strategy")
def get_strategy() -> dict[str, Any]:
    return load_strategy().model_dump()


@router.put("/config/strategy")
def update_strategy(payload: StrategyConfig) -> dict[str, Any]:
    for group in (payload.risk_weights, payload.ranking_weights):
        if abs(sum(group.values()) - 1.0) > 0.001:
            raise HTTPException(400, "每组权重之和必须等于 1.0")
    save_strategy(payload)
    return {"success": True, "strategy": payload.model_dump()}
