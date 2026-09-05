from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.route_estimates import api
from app.route_estimates.config import RouteEstimateSettings
from app.route_estimates.freightos import FreightosEstimate, FreightosProviderError, FreightosPublicProvider
from app.route_estimates.models import RouteEstimateRequest
from app.route_estimates.service import CacheEntry, RouteEstimateService
from app.route_estimates.telemetry import (
    STAGE_OUTCOMES,
    elapsed_bucket,
    record_route_estimate_stage,
    route_estimate_telemetry,
)


FULL_PROVIDER_RESPONSE = {
    "response": {
        "estimatedFreightRates": {
            "mode": {
                "mode": "FCL",
                "price": {
                    "min": {"moneyAmount": {"amount": "100", "currency": "USD"}},
                    "max": {"moneyAmount": {"amount": "200", "currency": "USD"}},
                },
                "transitTimes": {"min": "3", "max": "5", "unit": "days"},
            }
        }
    }
}

PARTIAL_PROVIDER_RESPONSE = {
    "response": {
        "estimatedFreightRates": {
            "mode": {
                "mode": "FCL",
                "price": {
                    "min": {"moneyAmount": {"amount": "100", "currency": "USD"}},
                    "max": {"moneyAmount": {"amount": "200", "currency": "USD"}},
                },
            }
        }
    }
}

VALID_REQUEST = {
    "contractVersion": "route-estimates-v1",
    "originPortId": "PORT-CNSHG",
    "destinationPortId": "PORT-USLAX",
    "cargo": {
        "quantity": 1,
        "shipmentMethod": "container",
        "containerType": "40hc",
        "grossWeightKg": 1000,
        "volumeM3": 10,
    },
}

ALLOWED_TELEMETRY_KEYS = {"event", "stage", "outcome", "elapsedBucket"}
ALLOWED_BUCKETS = {
    "lt_100ms",
    "100_499ms",
    "500_1999ms",
    "2_4s",
    "5_9s",
    "10_29s",
    "gte_30s",
    "unknown",
}


class FixtureProvider:
    def __init__(self, result: FreightosEstimate | None = None, error: FreightosProviderError | None = None):
        self.result = result or FreightosEstimate("USD", 100, 200, 3, 5, "a" * 64)
        self.error = error
        self.calls = 0

    async def estimate(self, _parameters: dict[str, Any], _request_id: str, _fingerprint: str) -> FreightosEstimate:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class LocationFixture:
    def __init__(self):
        self.calls = 0

    def __call__(self, _query: str, parameters: dict[str, Any] | None) -> list[dict[str, Any]]:
        self.calls += 1
        location_id = str((parameters or {}).get("location_id"))
        mapping = {"PORT-CNSHG": "CNSHG", "PORT-USLAX": "USLAX"}
        if location_id not in mapping:
            return []
        return [{
            "location_id": location_id,
            "location_id_version": "location-id-v2",
            "canonical_unlocode": mapping[location_id],
        }]


@pytest.fixture(autouse=True)
def fail_closed_real_http(monkeypatch):
    async def blocked_request(*_args: Any, **_kwargs: Any):
        raise AssertionError("real network access is forbidden in telemetry fixtures")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_request)


def telemetry_events(caplog: pytest.LogCaptureFixture) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for record in caplog.records:
        try:
            value = json.loads(record.getMessage())
        except json.JSONDecodeError:
            continue
        if value.get("event") == "route_estimate_stage":
            assert set(value) == ALLOWED_TELEMETRY_KEYS
            assert value["elapsedBucket"] in ALLOWED_BUCKETS
            events.append(value)
    return events


def event_pairs(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str]]:
    return [(event["stage"], event["outcome"]) for event in telemetry_events(caplog)]


def client_for(
    monkeypatch: pytest.MonkeyPatch,
    service: RouteEstimateService,
) -> TestClient:
    application = FastAPI()
    application.include_router(api.router)
    monkeypatch.setattr(api, "_service", service)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ROUTE_ESTIMATE_SERVICE_TOKEN", "fixture-service-token")
    return TestClient(application)


def post(client: TestClient, token: str = "fixture-service-token", payload: dict[str, Any] | None = None):
    return client.post(
        "/api/route-estimates/v1",
        json=payload or VALID_REQUEST,
        headers={"X-Route-Estimate-Token": token},
    )


def request_model() -> RouteEstimateRequest:
    return RouteEstimateRequest.model_validate(VALID_REQUEST)


def test_success_records_exact_stages_and_redacted_four_field_events(monkeypatch, caplog):
    locations = LocationFixture()
    provider = FixtureProvider()
    client = client_for(monkeypatch, RouteEstimateService(locations, provider=provider))
    caplog.set_level(logging.INFO, logger="app.route_estimates.telemetry")

    response = post(client)

    assert response.status_code == 200
    assert locations.calls == 2
    assert provider.calls == 1
    assert event_pairs(caplog) == [
        ("request_received", "completed"),
        ("authentication", "accepted"),
        ("location_resolution", "completed"),
        ("provider_cache", "miss"),
        ("provider_call", "called"),
        ("provider_call", "available"),
        ("response_mapping", "completed"),
        ("completed", "completed"),
    ]
    serialized = json.dumps(telemetry_events(caplog))
    for forbidden in [
        "fixture-service-token",
        "PORT-CNSHG",
        "PORT-USLAX",
        "CNSHG",
        "USLAX",
        "grossWeightKg",
        "requestId",
        "fingerprint",
        "http://",
        "https://",
    ]:
        assert forbidden not in serialized


def test_authentication_rejection_and_unconfigured_service_stop_before_data(monkeypatch, caplog):
    locations = LocationFixture()
    provider = FixtureProvider()
    client = client_for(monkeypatch, RouteEstimateService(locations, provider=provider))
    caplog.set_level(logging.INFO, logger="app.route_estimates.telemetry")

    rejected = post(client, token="wrong-fixture-token")
    assert rejected.status_code == 401
    assert event_pairs(caplog) == [
        ("request_received", "completed"),
        ("authentication", "rejected"),
        ("response_mapping", "client_error"),
        ("completed", "client_error"),
    ]
    assert locations.calls == 0
    assert provider.calls == 0

    caplog.clear()
    monkeypatch.setenv("ROUTE_ESTIMATE_SERVICE_TOKEN", "")
    unavailable = post(client, token="")
    assert unavailable.status_code == 503
    assert ("authentication", "configuration_unavailable") in event_pairs(caplog)
    assert ("completed", "service_unavailable") in event_pairs(caplog)
    assert locations.calls == 0
    assert provider.calls == 0


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], "unknown_port"),
        ([
            {"location_id": "PORT-CNSHG", "location_id_version": "location-id-v2", "canonical_unlocode": "CNSHG"},
            {"location_id": "PORT-CNSHG", "location_id_version": "location-id-v2", "canonical_unlocode": "CNSHG"},
        ], "ambiguous_port"),
        ([{"location_id": "PORT-CNSHG", "location_id_version": "legacy", "canonical_unlocode": "CNSHG"}], "invalid_identity"),
        ([{"location_id": "PORT-CNSHG", "location_id_version": "location-id-v2", "canonical_unlocode": ""}], "missing_unlocode"),
    ],
)
def test_location_failures_are_bounded_and_stop_provider(monkeypatch, caplog, rows, expected):
    calls = 0

    def query(_query: str, _parameters: dict[str, Any] | None):
        nonlocal calls
        calls += 1
        return rows

    provider = FixtureProvider()
    client = client_for(monkeypatch, RouteEstimateService(query, provider=provider))
    caplog.set_level(logging.INFO, logger="app.route_estimates.telemetry")

    response = post(client)

    assert response.status_code == 422
    assert calls == 1
    assert provider.calls == 0
    assert ("location_resolution", expected) in event_pairs(caplog)
    assert ("response_mapping", "client_error") in event_pairs(caplog)


def test_location_registry_unavailable_is_service_unavailable(monkeypatch, caplog):
    def query(_query: str, _parameters: dict[str, Any] | None):
        raise RuntimeError("fixture registry unavailable")

    client = client_for(monkeypatch, RouteEstimateService(query, provider=FixtureProvider()))
    caplog.set_level(logging.INFO, logger="app.route_estimates.telemetry")

    response = post(client)

    assert response.status_code == 503
    assert ("location_resolution", "unavailable") in event_pairs(caplog)
    assert ("response_mapping", "service_unavailable") in event_pairs(caplog)
    assert ("completed", "service_unavailable") in event_pairs(caplog)


def test_cache_miss_then_hit_does_not_repeat_provider(monkeypatch, caplog):
    locations = LocationFixture()
    provider = FixtureProvider()
    service = RouteEstimateService(locations, provider=provider)
    client = client_for(monkeypatch, service)
    caplog.set_level(logging.INFO, logger="app.route_estimates.telemetry")

    assert post(client).status_code == 200
    first = event_pairs(caplog)
    assert ("provider_cache", "miss") in first
    assert ("provider_call", "called") in first
    caplog.clear()

    assert post(client).status_code == 200
    second = event_pairs(caplog)
    assert ("provider_cache", "hit") in second
    assert ("provider_call", "not_called") in second
    assert provider.calls == 1


def test_roro_records_skipped_location_cache_and_provider(monkeypatch, caplog):
    locations = LocationFixture()
    provider = FixtureProvider()
    client = client_for(monkeypatch, RouteEstimateService(locations, provider=provider))
    caplog.set_level(logging.INFO, logger="app.route_estimates.telemetry")
    payload = {
        **VALID_REQUEST,
        "cargo": {"quantity": 1, "shipmentMethod": "roro", "grossWeightKg": 1000},
    }

    response = post(client, payload=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "unsupported"
    assert locations.calls == 0
    assert provider.calls == 0
    assert ("location_resolution", "not_reached") in event_pairs(caplog)
    assert ("provider_cache", "not_reached") in event_pairs(caplog)
    assert ("provider_call", "not_called") in event_pairs(caplog)
    assert ("provider_call", "unsupported") in event_pairs(caplog)


@pytest.mark.parametrize(
    ("kind", "expected_outcome", "expected_status"),
    [
        ("available", "available", "available"),
        ("partial", "partial", "partial"),
        ("timeout", "timeout", "unavailable"),
        ("network", "network", "unavailable"),
        ("http_4xx", "http_4xx", "unavailable"),
        ("http_5xx", "http_5xx", "unavailable"),
        ("invalid_response", "invalid_response", "unavailable"),
    ],
)
def test_mock_transport_classifies_provider_outcomes(monkeypatch, caplog, kind, expected_outcome, expected_status):
    def handler(request: httpx.Request) -> httpx.Response:
        if kind == "available":
            return httpx.Response(200, json=FULL_PROVIDER_RESPONSE, request=request)
        if kind == "partial":
            return httpx.Response(200, json=PARTIAL_PROVIDER_RESPONSE, request=request)
        if kind == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        if kind == "network":
            raise httpx.ConnectError("fixture network", request=request)
        if kind == "http_4xx":
            return httpx.Response(401, json={}, request=request)
        if kind == "http_5xx":
            return httpx.Response(503, json={}, request=request)
        return httpx.Response(200, text="not-json", request=request)

    calls = 0

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        nonlocal calls

        def counted_handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return handler(request)

        return httpx.AsyncClient(transport=httpx.MockTransport(counted_handler), **kwargs)

    settings = RouteEstimateSettings(max_retries=0)
    provider = FreightosPublicProvider(settings, client_factory=client_factory)
    client = client_for(monkeypatch, RouteEstimateService(LocationFixture(), provider=provider, settings=settings))
    caplog.set_level(logging.INFO, logger="app.route_estimates.telemetry")

    response = post(client)

    assert response.status_code == 200
    assert response.json()["status"] == expected_status
    assert calls == 1
    assert ("provider_call", "called") in event_pairs(caplog)
    assert ("provider_call", expected_outcome) in event_pairs(caplog)


def test_expired_cache_circuit_breaker_rate_limit_and_generic_failure_are_bounded(caplog):
    caplog.set_level(logging.INFO, logger="app.route_estimates.telemetry")
    payload = request_model()

    expired_provider = FixtureProvider()
    expired_service = RouteEstimateService(LocationFixture(), provider=expired_provider)
    initial = asyncio.run(expired_service.estimate(payload))
    fingerprint = initial.request_fingerprint
    expired_service.cache[fingerprint] = CacheEntry(
        time.monotonic() - 1,
        expired_service.cache[fingerprint].response,
    )
    expired_provider.error = FreightosProviderError("fixture", "unavailable")
    caplog.clear()
    with route_estimate_telemetry():
        result = asyncio.run(expired_service.estimate(payload))
    assert result.status == "expired"
    assert ("provider_cache", "expired") in event_pairs(caplog)
    assert ("provider_call", "unavailable") in event_pairs(caplog)

    caplog.clear()
    circuit_service = RouteEstimateService(LocationFixture(), provider=FixtureProvider())
    circuit_service.circuit_open_until = time.monotonic() + 60
    with route_estimate_telemetry():
        asyncio.run(circuit_service.estimate(payload))
    assert ("provider_call", "circuit_open") in event_pairs(caplog)

    caplog.clear()
    limited_settings = RouteEstimateSettings(hourly_request_limit=1)
    limited_service = RouteEstimateService(LocationFixture(), provider=FixtureProvider(), settings=limited_settings)
    limited_service.request_times.append(time.monotonic())
    with route_estimate_telemetry():
        asyncio.run(limited_service.estimate(payload))
    assert ("provider_call", "rate_limited") in event_pairs(caplog)


def test_elapsed_buckets_context_boundary_and_all_frozen_stage_outcomes(caplog):
    assert [elapsed_bucket(value) for value in [None, 0, 99, 100, 499, 500, 1999, 2000, 4999, 5000, 9999, 10000, 29999, 30000]] == [
        "unknown",
        "lt_100ms",
        "lt_100ms",
        "100_499ms",
        "100_499ms",
        "500_1999ms",
        "500_1999ms",
        "2_4s",
        "2_4s",
        "5_9s",
        "5_9s",
        "10_29s",
        "10_29s",
        "gte_30s",
    ]
    assert record_route_estimate_stage("request_received", "completed") is False

    caplog.set_level(logging.INFO, logger="app.route_estimates.telemetry")
    clock = iter([0.0] + [0.0] * sum(len(outcomes) for outcomes in STAGE_OUTCOMES.values()))
    with route_estimate_telemetry(lambda: next(clock)):
        for stage, outcomes in STAGE_OUTCOMES.items():
            for outcome in outcomes:
                assert record_route_estimate_stage(stage, outcome) is True
    assert len(telemetry_events(caplog)) == sum(len(outcomes) for outcomes in STAGE_OUTCOMES.values())
    with route_estimate_telemetry(lambda: 0.0):
        with pytest.raises(ValueError):
            record_route_estimate_stage("provider_call", "free_text_error")
