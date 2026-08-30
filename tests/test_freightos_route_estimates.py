from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.route_estimates import api
from app.route_estimates.config import FREIGHTOS_PUBLIC_ENDPOINT, RouteEstimateSettings
from app.route_estimates.freightos import (
    FreightosEstimate,
    FreightosProviderError,
    FreightosPublicProvider,
    build_freightos_parameters,
    parse_freightos_response,
)
from app.route_estimates.locations import LocationResolutionError, resolve_port_unlocode
from app.route_estimates.models import RouteEstimateCargo, RouteEstimateRequest, RouteEstimateResponse
from app.route_estimates.service import RouteEstimateService


FULL_RESPONSE = {
    "response": {
        "estimatedFreightRates": {
            "numQuotes": "1",
            "mode": {
                "mode": "FCL",
                "price": {
                    "min": {"moneyAmount": {"amount": "3262", "currency": "USD"}},
                    "max": {"moneyAmount": {"amount": "3554", "currency": "USD"}},
                },
                "transitTimes": {"min": "17", "max": "21", "unit": "days"},
            },
        }
    }
}


def request_payload(**cargo_overrides: Any) -> RouteEstimateRequest:
    cargo = {
        "quantity": 2,
        "shipmentMethod": "container",
        "containerType": "40ft",
        "grossWeightKg": 15000,
    }
    cargo.update(cargo_overrides)
    return RouteEstimateRequest.model_validate(
        {
            "contractVersion": "route-estimates-v1",
            "originPortId": "PORT-CNSHG",
            "destinationPortId": "PORT-USLGB",
            "cargo": cargo,
        }
    )


def location_query(_: str, parameters: dict[str, Any] | None) -> list[dict[str, Any]]:
    mapping = {"PORT-CNSHG": "CNSHG", "PORT-USLAX": "USLAX", "PORT-USLGB": "USLGB"}
    location_id = parameters["location_id"]
    if location_id not in mapping:
        return []
    return [
        {
            "location_id": location_id,
            "location_id_version": "location-id-v2",
            "canonical_unlocode": mapping[location_id],
        }
    ]


class FakeProvider:
    def __init__(self, result: FreightosEstimate | None = None, error: Exception | None = None):
        self.result = result or FreightosEstimate("USD", 100, 200, 3, 5, "a" * 64)
        self.error = error
        self.calls = 0

    async def estimate(self, parameters: dict[str, Any], request_id: str, fingerprint: str) -> FreightosEstimate:
        self.calls += 1
        await asyncio.sleep(0)
        if self.error:
            raise self.error
        return self.result


@pytest.mark.parametrize(
    ("container_type", "loadtype"),
    [("20ft", "container20"), ("40ft", "container40"), ("40hc", "container40HC")],
)
def test_container_parameter_mapping(container_type, loadtype):
    payload = request_payload(containerType=container_type)
    parameters = build_freightos_parameters("CNSHG", "USLGB", payload.cargo)
    assert parameters["loadtype"] == loadtype
    assert parameters["mode"] == "FCL"
    assert parameters["weight"] == 7500
    assert parameters["origin"] == "CNSHG"
    assert "url" not in parameters


def test_lcl_parameter_mapping_and_boundaries():
    cargo = RouteEstimateCargo.model_validate(
        {
            "quantity": 4,
            "shipmentMethod": "lcl",
            "grossWeightKg": 800,
            "volumeM3": 4,
            "lengthCm": 100,
            "widthCm": 50,
            "heightCm": 50,
        }
    )
    parameters = build_freightos_parameters("CNSHG", "USLGB", cargo)
    assert parameters["loadtype"] == "boxes"
    assert parameters["mode"] == "LCL"
    assert parameters["weight"] == 200
    assert parameters["volume"] == "1.000000cbm"
    with pytest.raises(ValueError):
        RouteEstimateCargo.model_validate({"quantity": 0, "shipmentMethod": "lcl", "grossWeightKg": 1, "volumeM3": 1})
    with pytest.raises(ValueError):
        RouteEstimateCargo.model_validate({"quantity": 1, "shipmentMethod": "lcl", "grossWeightKg": 100001, "volumeM3": 1})


def test_location_id_v2_resolves_only_audited_canonical_unlocode():
    assert resolve_port_unlocode("PORT-CNSHG", location_query) == "CNSHG"
    assert resolve_port_unlocode("PORT-USLAX", location_query) == "USLAX"
    with pytest.raises(LocationResolutionError) as unknown:
        resolve_port_unlocode("PORT-XXXXX", location_query)
    assert unknown.value.code == "unknown_port"

    def ambiguous_query(*_: Any) -> list[dict[str, Any]]:
        row = {"location_id": "PORT-CNSHG", "location_id_version": "location-id-v2", "canonical_unlocode": "CNSHG"}
        return [row, row]

    with pytest.raises(LocationResolutionError) as ambiguous:
        resolve_port_unlocode("PORT-CNSHG", ambiguous_query)
    assert ambiguous.value.code == "ambiguous_port"


def test_response_parsing_preserves_ranges_without_most_likely():
    result = parse_freightos_response(FULL_RESPONSE, "FCL")
    assert (result.cost_min, result.cost_max) == (3262, 3554)
    assert (result.transit_min_days, result.transit_max_days) == (17, 21)
    assert not hasattr(result, "most_likely")


def test_partial_response_and_conservative_ranking():
    provider = FakeProvider(FreightosEstimate("USD", 100, 200, None, None, "b" * 64))
    service = RouteEstimateService(location_query, provider=provider)
    result = asyncio.run(service.estimate(request_payload()))
    assert result.status == "partial"
    assert result.cost_range.max == 200
    assert result.ranking_cost_usd == 200
    assert result.ranking_duration_days is None
    assert result.ranking_policy == "conservative-upper-bound-v1"
    assert "transitTimeRange" in result.missing_data
    assert result.attribution_url == "https://ship.freightos.com"


def test_roro_is_explicitly_unsupported_without_provider_call():
    provider = FakeProvider()
    service = RouteEstimateService(location_query, provider=provider)
    result = asyncio.run(service.estimate(request_payload(shipmentMethod="roro", containerType=None)))
    assert result.status == "unsupported"
    assert result.cost_range.max is None
    assert provider.calls == 0


def test_cache_hit_and_single_flight_request_deduplication():
    provider = FakeProvider()
    service = RouteEstimateService(location_query, provider=provider)

    async def run() -> list[Any]:
        payload = request_payload()
        return await asyncio.gather(service.estimate(payload), service.estimate(payload))

    first, second = asyncio.run(run())
    assert provider.calls == 1
    assert {first.cache_status, second.cache_status} == {"miss", "hit"}


def test_expired_cache_is_not_returned_when_refresh_fails():
    provider = FakeProvider()
    settings = RouteEstimateSettings(cache_ttl_seconds=30, max_retries=0)
    service = RouteEstimateService(location_query, provider=provider, settings=settings)
    payload = request_payload()
    first = asyncio.run(service.estimate(payload))
    service.cache[first.request_fingerprint].expires_monotonic = time.monotonic() - 1
    provider.error = FreightosProviderError("offline")
    expired = asyncio.run(service.estimate(payload))
    assert expired.status == "expired"
    assert expired.cache_status == "expired"
    assert expired.cost_range.max is None


@pytest.mark.parametrize("failure", ["429", "500", "timeout", "non-json"])
def test_provider_failures_are_unavailable(failure):
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if failure == "non-json":
            return httpx.Response(200, text="not-json", request=request)
        return httpx.Response(int(failure), json={"error": "temporary"}, request=request)

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    settings = RouteEstimateSettings(max_retries=0)
    provider = FreightosPublicProvider(settings, client_factory=client_factory)
    service = RouteEstimateService(location_query, provider=provider, settings=settings)
    result = asyncio.run(service.estimate(request_payload()))
    assert result.status == "unavailable"
    assert result.response_sha256 is None


def test_malformed_provider_response_is_rejected():
    with pytest.raises(FreightosProviderError):
        parse_freightos_response({"response": {"estimatedFreightRates": {"mode": {"mode": "FCL"}}}}, "FCL")


def test_logs_do_not_contain_url_query_or_cargo(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=FULL_RESPONSE, request=request)

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    provider = FreightosPublicProvider(RouteEstimateSettings(max_retries=0), client_factory=client_factory)
    with caplog.at_level(logging.INFO):
        asyncio.run(provider.estimate(build_freightos_parameters("CNSHG", "USLGB", request_payload().cargo), "request-1", "f" * 64))
    output = caplog.text
    assert FREIGHTOS_PUBLIC_ENDPOINT not in output
    assert "CNSHG" not in output
    assert "15000" not in output
    assert "request-1" in output


def test_api_requires_auth_rejects_unknown_fields_and_duplicate_keys(monkeypatch):
    application = FastAPI()
    application.include_router(api.router)
    provider = FakeProvider()
    monkeypatch.setattr(api, "_service", RouteEstimateService(location_query, provider=provider))
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ROUTE_ESTIMATE_SERVICE_TOKEN", "test-only-token")
    client = TestClient(application)
    payload = request_payload().model_dump(mode="json", by_alias=True)
    assert client.post("/api/route-estimates/v1", json=payload).status_code == 401
    headers = {"X-Route-Estimate-Token": "test-only-token"}
    assert client.post("/api/route-estimates/v1", json={**payload, "upstreamUrl": "https://example.com"}, headers=headers).status_code == 422
    duplicate = b'{"contractVersion":"route-estimates-v1","contractVersion":"other"}'
    assert client.post("/api/route-estimates/v1", content=duplicate, headers=headers).status_code == 422


def test_provider_failure_is_not_reported_as_neo4j_failure():
    provider = FakeProvider(error=FreightosProviderError("offline"))
    service = RouteEstimateService(location_query, provider=provider)
    result = asyncio.run(service.estimate(request_payload()))
    assert result.status == "unavailable"
    assert "neo4j" not in " ".join(result.missing_data).casefold()


def test_malformed_content_length_is_rejected():
    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/route-estimates/v1",
            "headers": [(b"content-length", b"not-a-number")],
            "client": ("127.0.0.1", 12345),
        },
        receive,
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(api.parse_request(request))
    assert error.value.status_code == 422


def test_oversized_provider_response_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1_048_577, request=request)

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    settings = RouteEstimateSettings(max_retries=0)
    provider = FreightosPublicProvider(settings, client_factory=client_factory)
    service = RouteEstimateService(location_query, provider=provider, settings=settings)
    result = asyncio.run(service.estimate(request_payload()))
    assert result.status == "unavailable"
    assert result.response_sha256 is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("FREIGHTOS_TIMEOUT_SECONDS", "0"),
        ("FREIGHTOS_MAX_RETRIES", "6"),
        ("FREIGHTOS_CACHE_TTL_SECONDS", "0"),
        ("FREIGHTOS_HOURLY_REQUEST_LIMIT", "101"),
        ("FREIGHTOS_CIRCUIT_FAILURE_THRESHOLD", "0"),
        ("FREIGHTOS_CIRCUIT_OPEN_SECONDS", "3601"),
        ("ROUTE_ESTIMATE_ALLOW_LOCAL_UNAUTHENTICATED", "sometimes"),
    ],
)
def test_invalid_environment_ranges_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        RouteEstimateSettings()


def test_expired_cache_and_fingerprint_lock_are_cleaned_up():
    service = RouteEstimateService(location_query, provider=FakeProvider())
    result = asyncio.run(service.estimate(request_payload()))
    fingerprint = result.request_fingerprint
    service.cache[fingerprint].expires_monotonic = time.monotonic() - 1
    assert fingerprint in service.fingerprint_locks

    async def cleanup() -> None:
        await service._prepare_lock("c" * 64)

    asyncio.run(cleanup())
    assert fingerprint not in service.cache
    assert fingerprint not in service.fingerprint_locks


def test_authentication_token_is_not_logged_or_returned(monkeypatch, caplog):
    application = FastAPI()
    application.include_router(api.router)
    monkeypatch.setattr(api, "_service", RouteEstimateService(location_query, provider=FakeProvider()))
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ROUTE_ESTIMATE_SERVICE_TOKEN", "fixture-secret-never-log")
    payload = request_payload().model_dump(mode="json", by_alias=True)
    client = TestClient(application)
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/api/route-estimates/v1",
            json=payload,
            headers={"X-Route-Estimate-Token": "fixture-secret-never-log"},
        )
    assert response.status_code == 200
    assert "fixture-secret-never-log" not in caplog.text
    assert "fixture-secret-never-log" not in response.text


def test_frozen_json_schemas_match_pydantic_contract():
    contract_dir = Path("contracts/route-estimates-v1")
    request_schema = json.loads((contract_dir / "request.schema.json").read_text(encoding="utf-8"))
    response_schema = json.loads((contract_dir / "response.schema.json").read_text(encoding="utf-8"))
    assert request_schema == RouteEstimateRequest.model_json_schema(by_alias=True)
    assert response_schema == RouteEstimateResponse.model_json_schema(by_alias=True)


def test_frozen_openapi_contract_and_status_fixtures():
    contract = json.loads(Path("contracts/route-estimates-v1/openapi.json").read_text(encoding="utf-8"))
    operation = contract["paths"]["/api/route-estimates/v1"]["post"]
    assert operation["parameters"][0]["name"] == "X-Route-Estimate-Token"
    assert set(operation["responses"]) == {"200", "401", "413", "422", "503"}
    fixture_dir = Path("tests/fixtures/route_estimates_v1")
    for status in ("available", "partial", "unsupported", "unavailable", "expired"):
        payload = json.loads((fixture_dir / f"{status}.json").read_text(encoding="utf-8"))
        response = RouteEstimateResponse.model_validate(payload)
        assert response.status == status
        assert response.attribution_url == "https://ship.freightos.com"
        assert "mostLikely" not in payload


def test_runtime_openapi_matches_frozen_auth_and_http_statuses():
    application = FastAPI()
    application.include_router(api.router)
    runtime = application.openapi()["paths"]["/api/route-estimates/v1"]["post"]
    frozen = json.loads(Path("contracts/route-estimates-v1/openapi.json").read_text(encoding="utf-8"))
    contract = frozen["paths"]["/api/route-estimates/v1"]["post"]
    assert runtime["parameters"] == contract["parameters"]
    assert set(runtime["responses"]) == set(contract["responses"])
