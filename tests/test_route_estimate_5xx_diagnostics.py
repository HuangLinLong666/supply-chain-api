from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.route_estimates import api
from app.route_estimates.config import RouteEstimateSettings
from app.route_estimates.diagnostics import DOWNSTREAM_OUTCOME_HEADER, DOWNSTREAM_STAGE_HEADER
from app.route_estimates.freightos import FreightosProviderError
from app.route_estimates.service import RouteEstimateService
from app.route_estimates.telemetry import configure_route_estimate_telemetry_logging, logger as telemetry_logger
from database import neo4j_client


REQUEST = {
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
HEALTH_KEYS = {
    "status",
    "configurationStatus",
    "authenticationStatus",
    "locationRegistryStatus",
    "providerStatus",
}


class NeverProvider:
    calls = 0

    async def estimate(self, *_args: Any, **_kwargs: Any):
        self.calls += 1
        raise AssertionError("provider must not be reached")


class FailedProvider:
    calls = 0

    async def estimate(self, *_args: Any, **_kwargs: Any):
        self.calls += 1
        raise FreightosProviderError("fixture-only", "http_5xx")


class InvalidResponseService:
    async def estimate(self, _payload: Any):
        return {"status": "available"}


class UnexpectedFailureService:
    async def estimate(self, _payload: Any):
        raise KeyError("fixture-only")


def valid_query(_query: str, parameters: dict[str, Any] | None, **_kwargs: Any):
    location_id = str((parameters or {}).get("location_id"))
    mapping = {"PORT-CNSHG": "CNSHG", "PORT-USLAX": "USLAX"}
    return [{
        "location_id": location_id,
        "location_id_version": "location-id-v2",
        "canonical_unlocode": mapping[location_id],
    }]


@pytest.fixture(autouse=True)
def safe_environment(monkeypatch):
    async def blocked_network(*_args: Any, **_kwargs: Any):
        raise AssertionError("real network is forbidden")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_network)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ROUTE_ESTIMATE_SERVICE_TOKEN", "fixture-token")
    monkeypatch.setenv("ROUTE_ESTIMATE_ALLOW_LOCAL_UNAUTHENTICATED", "false")
    monkeypatch.setenv("FREIGHTOS_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("FREIGHTOS_MAX_RETRIES", "0")
    monkeypatch.setenv("FREIGHTOS_CACHE_TTL_SECONDS", "900")
    monkeypatch.setenv("FREIGHTOS_HOURLY_REQUEST_LIMIT", "100")
    monkeypatch.setenv("FREIGHTOS_CIRCUIT_FAILURE_THRESHOLD", "3")
    monkeypatch.setenv("FREIGHTOS_CIRCUIT_OPEN_SECONDS", "300")


def application_client(monkeypatch, service: Any, query=valid_query) -> TestClient:
    application = FastAPI()
    application.include_router(api.router)
    monkeypatch.setattr(api, "_service", service)
    monkeypatch.setattr(api, "run_query", query)
    return TestClient(application, raise_server_exceptions=False)


def post(client: TestClient, token: str = "fixture-token"):
    return client.post(
        "/api/route-estimates/v1",
        json=REQUEST,
        headers={"X-Route-Estimate-Token": token},
    )


def classification(response) -> tuple[str | None, str | None]:
    return response.headers.get(DOWNSTREAM_STAGE_HEADER), response.headers.get(DOWNSTREAM_OUTCOME_HEADER)


def test_token_states_are_bounded(monkeypatch):
    service = RouteEstimateService(valid_query, provider=NeverProvider())
    client = application_client(monkeypatch, service)
    rejected = post(client, "wrong")
    assert rejected.status_code == 401
    assert classification(rejected) == ("authentication", "authentication_rejected")

    monkeypatch.setenv("ROUTE_ESTIMATE_SERVICE_TOKEN", "")
    unavailable = post(client, "")
    assert unavailable.status_code == 503
    assert classification(unavailable) == ("authentication", "authentication_configuration_unavailable")


def test_invalid_settings_are_classified_as_503(monkeypatch):
    service = RouteEstimateService(valid_query, provider=NeverProvider())
    monkeypatch.setenv("FREIGHTOS_TIMEOUT_SECONDS", "0")
    response = post(application_client(monkeypatch, service))
    assert response.status_code == 503
    assert classification(response) == ("authentication", "route_estimate_settings_invalid")


def test_service_initialization_failure_is_classified(monkeypatch):
    monkeypatch.setattr(api, "_service", None)

    class BrokenService:
        def __init__(self, *_args: Any, **_kwargs: Any):
            raise ValueError("fixture-only")

    monkeypatch.setattr(api, "RouteEstimateService", BrokenService)
    application = FastAPI()
    application.include_router(api.router)
    response = post(TestClient(application, raise_server_exceptions=False))
    assert response.status_code == 503
    assert classification(response) == ("service_initialization", "service_initialization_failed")


def test_location_registry_failure_is_classified(monkeypatch):
    def unavailable_query(_query: str, _parameters: dict[str, Any] | None):
        raise RuntimeError("fixture-only")

    service = RouteEstimateService(unavailable_query, provider=NeverProvider())
    response = post(application_client(monkeypatch, service))
    assert response.status_code == 503
    assert classification(response) == ("location_resolution", "location_registry_unavailable")


@pytest.mark.parametrize(
    ("service", "stage", "outcome"),
    [
        (InvalidResponseService(), "response_validation", "response_validation_failed"),
        (UnexpectedFailureService(), "internal", "unhandled_internal_error"),
    ],
)
def test_internal_failures_are_generic_and_classified(monkeypatch, service, stage, outcome):
    response = post(application_client(monkeypatch, service))
    assert response.status_code == 500
    assert classification(response) == (stage, outcome)
    assert "fixture-only" not in response.text


def test_provider_failure_remains_200_unavailable(monkeypatch):
    provider = FailedProvider()
    response = post(application_client(monkeypatch, RouteEstimateService(valid_query, provider=provider)))
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert classification(response) == (None, None)
    assert provider.calls == 1


def test_health_ready_checks_two_ports_without_provider(monkeypatch):
    calls = 0
    timeouts: list[float | None] = []

    def query(query_text: str, parameters: dict[str, Any] | None, *, timeout_seconds: float | None = None):
        nonlocal calls
        calls += 1
        timeouts.append(timeout_seconds)
        return valid_query(query_text, parameters)

    client = application_client(monkeypatch, RouteEstimateService(valid_query, provider=NeverProvider()), query)
    response = client.get("/health/route-estimates")
    assert response.status_code == 200
    assert set(response.json()) == HEALTH_KEYS
    assert response.json() == {
        "status": "ready",
        "configurationStatus": "valid",
        "authenticationStatus": "configured",
        "locationRegistryStatus": "ready",
        "providerStatus": "not_called",
    }
    assert calls == 2
    assert timeouts == [5.0, 5.0]


def test_neo4j_read_timeout_is_attached_without_network(monkeypatch):
    observed: dict[str, Any] = {}

    class Result:
        def __iter__(self):
            return iter(())

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args: Any):
            return None

        def run(self, query: Any, parameters: dict[str, Any]):
            observed["query"] = query
            observed["parameters"] = parameters
            return Result()

    class Driver:
        def session(self, **_kwargs: Any):
            return Session()

    monkeypatch.setattr(
        neo4j_client,
        "get_settings",
        lambda: neo4j_client.Neo4jSettings("fixture", "fixture", "fixture", None),
    )
    monkeypatch.setattr(neo4j_client, "get_driver", lambda: Driver())

    assert neo4j_client.run_query("RETURN 1", {}, timeout_seconds=5.0) == []
    assert str(observed["query"]) == "RETURN 1"
    assert observed["query"].timeout == 5.0
    assert observed["parameters"] == {}


@pytest.mark.parametrize(
    ("environment_name", "environment_value", "expected_field", "expected_value"),
    [
        ("FREIGHTOS_TIMEOUT_SECONDS", "0", "configurationStatus", "invalid"),
        ("ROUTE_ESTIMATE_SERVICE_TOKEN", "", "authenticationStatus", "unconfigured"),
    ],
)
def test_health_stops_before_database_when_configuration_is_unavailable(
    monkeypatch, environment_name, environment_value, expected_field, expected_value
):
    calls = 0
    service = RouteEstimateService(valid_query, provider=NeverProvider())

    def query(*_args: Any, **_kwargs: Any):
        nonlocal calls
        calls += 1
        raise AssertionError("database must not be reached")

    monkeypatch.setenv(environment_name, environment_value)
    client = application_client(monkeypatch, service, query)
    response = client.get("/health/route-estimates")
    assert response.status_code == 503
    assert set(response.json()) == HEALTH_KEYS
    assert response.json()[expected_field] == expected_value
    assert response.json()["providerStatus"] == "not_called"
    assert calls == 0


def test_health_classifies_invalid_identity_without_provider(monkeypatch):
    client = application_client(
        monkeypatch,
        RouteEstimateService(valid_query, provider=NeverProvider()),
        lambda *_args, **_kwargs: [],
    )
    response = client.get("/health/route-estimates")
    assert response.status_code == 503
    assert response.json()["locationRegistryStatus"] == "invalid_identity"
    assert response.json()["providerStatus"] == "not_called"


def test_telemetry_logger_configuration_is_exact_and_idempotent():
    original_handlers = list(telemetry_logger.handlers)
    original_level = telemetry_logger.level
    original_propagate = telemetry_logger.propagate
    try:
        telemetry_logger.handlers.clear()
        configure_route_estimate_telemetry_logging()
        configure_route_estimate_telemetry_logging()
        assert telemetry_logger.name == "app.route_estimates.telemetry"
        assert telemetry_logger.level == logging.INFO
        assert telemetry_logger.propagate is False
        assert len(telemetry_logger.handlers) == 1
    finally:
        telemetry_logger.handlers[:] = original_handlers
        telemetry_logger.setLevel(original_level)
        telemetry_logger.propagate = original_propagate
