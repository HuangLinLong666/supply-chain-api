from __future__ import annotations

import json
import secrets
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.route_estimates.config import RouteEstimateSettings
from app.route_estimates.diagnostics import (
    DOWNSTREAM_OUTCOME_HEADER,
    diagnostic_headers,
    route_estimate_health_snapshot,
)
from app.route_estimates.locations import LocationResolutionError
from app.route_estimates.models import RouteEstimateRequest, RouteEstimateResponse
from app.route_estimates.service import RouteEstimateService
from app.route_estimates.telemetry import record_route_estimate_stage, route_estimate_telemetry
from database.neo4j_client import run_query


router = APIRouter(tags=["Route Estimates"])
MAX_REQUEST_BYTES = 16_384
_service: RouteEstimateService | None = None


def get_service() -> RouteEstimateService:
    global _service
    if _service is None:
        _service = RouteEstimateService(run_query)
    return _service


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"重复 JSON 字段: {key}")
        result[key] = value
    return result


async def parse_request(request: Request) -> RouteEstimateRequest:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Content-Length 必须是非负整数") from exc
        if declared_size < 0:
            raise HTTPException(status_code=422, detail="Content-Length 必须是非负整数")
        if declared_size > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="请求体超过 16 KiB")
    body = await request.body()
    if len(body) > MAX_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="请求体超过 16 KiB")
    try:
        raw = json.loads(body.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return RouteEstimateRequest.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_context=False)) from exc


def authorize(request: Request, supplied_token: str | None) -> None:
    try:
        settings = RouteEstimateSettings()
    except (TypeError, ValueError):
        record_route_estimate_stage("authentication", "configuration_unavailable")
        raise HTTPException(
            status_code=503,
            detail="Route estimate service configuration is unavailable",
            headers=diagnostic_headers("authentication", "route_estimate_settings_invalid"),
        )
    if settings.service_token:
        if supplied_token is None or not secrets.compare_digest(supplied_token, settings.service_token):
            record_route_estimate_stage("authentication", "rejected")
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing route estimate service token",
                headers=diagnostic_headers("authentication", "authentication_rejected"),
            )
        record_route_estimate_stage("authentication", "accepted")
        return
    client_host = request.client.host if request.client else ""
    local_hosts = {"127.0.0.1", "::1", "localhost", "testclient"}
    if (
        settings.app_environment in {"development", "test"}
        and settings.allow_local_unauthenticated
        and client_host in local_hosts
    ):
        record_route_estimate_stage("authentication", "accepted")
        return
    record_route_estimate_stage("authentication", "configuration_unavailable")
    raise HTTPException(
        status_code=503,
        detail="Route estimate service authentication is not configured",
        headers=diagnostic_headers("authentication", "authentication_configuration_unavailable"),
    )


@router.get("/health/route-estimates", tags=["Service"])
def route_estimate_health() -> JSONResponse:
    def bounded_health_query(query: str, parameters: dict[str, Any] | None) -> list[dict[str, Any]]:
        return run_query(query, parameters, timeout_seconds=5.0)

    result = route_estimate_health_snapshot(bounded_health_query)
    return JSONResponse(status_code=200 if result["status"] == "ready" else 503, content=result)


@router.post(
    "/api/route-estimates/v1",
    response_model=RouteEstimateResponse,
    response_model_by_alias=True,
    summary="Get a Freightos public marketplace cost and transit estimate",
    responses={
        401: {"description": "Missing or invalid service token"},
        413: {"description": "Request body exceeds 16 KiB"},
        422: {"description": "Invalid request or non-unique audited port identity"},
        503: {"description": "Authentication or location registry is unavailable"},
    },
    openapi_extra={
        "parameters": [
            {
                "name": "X-Route-Estimate-Token",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
                "description": "Server-side service token; never expose to a browser or URL.",
            }
        ],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": RouteEstimateRequest.model_json_schema(by_alias=True),
                }
            },
        }
    },
)
async def route_estimate(
    request: Request,
    x_route_estimate_token: str | None = Header(default=None, include_in_schema=False),
) -> RouteEstimateResponse:
    with route_estimate_telemetry():
        record_route_estimate_stage("request_received", "completed")
        try:
            authorize(request, x_route_estimate_token)
            payload = await parse_request(request)
            try:
                service = get_service()
            except (TypeError, ValueError, RuntimeError) as exc:
                raise HTTPException(
                    status_code=503,
                    detail="Route estimate service is unavailable",
                    headers=diagnostic_headers("service_initialization", "service_initialization_failed"),
                ) from exc
            try:
                raw_response = await service.estimate(payload)
            except LocationResolutionError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"code": exc.code, "message": str(exc)},
                    headers=diagnostic_headers("location_resolution", "location_identity_invalid"),
                ) from exc
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=503,
                    detail="Location registry is unavailable",
                    headers=diagnostic_headers("location_resolution", "location_registry_unavailable"),
                ) from exc
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail="Route estimate service encountered an internal error",
                    headers=diagnostic_headers("internal", "unhandled_internal_error"),
                ) from exc
            try:
                response = RouteEstimateResponse.model_validate(raw_response)
            except ValidationError as exc:
                raise HTTPException(
                    status_code=500,
                    detail="Route estimate response is unavailable",
                    headers=diagnostic_headers("response_validation", "response_validation_failed"),
                ) from exc
        except HTTPException as exc:
            if not exc.headers or DOWNSTREAM_OUTCOME_HEADER not in exc.headers:
                exc.headers = diagnostic_headers("request_validation", "request_invalid")
            outcome = "service_unavailable" if exc.status_code >= 500 else "client_error"
            record_route_estimate_stage("response_mapping", outcome)
            record_route_estimate_stage("completed", outcome)
            raise
        record_route_estimate_stage("response_mapping", "completed")
        record_route_estimate_stage("completed", "completed")
        return response
