from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app import main
from app.recommendation.errors import ERROR_CONTRACT


BASE_REQUEST = {
    "origin": "PORT-CNSHG",
    "destination": "PORT-USLAX",
    "cargo": {"type": "finished_vehicle"},
    "strategy": "balanced",
    "constraints": {"allowedModes": ["road", "rail", "sea", "air"], "maxHops": 12},
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("ROUTE_RECOMMENDATION_TOKEN", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "test")
    return TestClient(main.app)


def request_body(**overrides):
    body = dict(BASE_REQUEST)
    body.update(overrides)
    return body


def assert_error(response, code):
    status, message = ERROR_CONTRACT[code]
    assert response.status_code == status
    assert response.json() == {"detail": {"code": code, "message": message}}


def test_invalid_constraints_is_only_required_modes_exceeding_max_hops(client, monkeypatch):
    monkeypatch.setattr(main, "route_graph_segments", lambda: pytest.fail("graph must not be queried"))
    response = client.post("/api/routes/recommend", json=request_body(constraints={
        "allowedModes": ["road", "rail", "sea"],
        "requiredModes": ["road", "rail", "sea", "sea"],
        "maxHops": 2,
    }))
    assert_error(response, "invalid_constraints")


@pytest.mark.parametrize("required_modes,max_hops", [(["road", "sea"], 2), (["road", "road"], 1)])
def test_required_modes_at_or_below_max_hops_is_not_invalid_constraints(
    client, monkeypatch, required_modes, max_hops
):
    monkeypatch.setattr(main, "route_graph_segments", lambda: [])
    response = client.post("/api/routes/recommend", json=request_body(constraints={
        "allowedModes": ["road", "sea"], "requiredModes": required_modes, "maxHops": max_hops,
    }))
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "origin_not_found"


@pytest.mark.parametrize("constraints", [
    {"allowedModes": []},
    {"allowedModes": ["sea"], "requiredModes": ["road"]},
])
def test_pydantic_constraint_errors_remain_standard_422(client, constraints):
    response = client.post("/api/routes/recommend", json=request_body(constraints=constraints))
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


@pytest.mark.parametrize("code", list(ERROR_CONTRACT))
def test_error_matrix_has_exact_status_and_envelope(code):
    status, message = ERROR_CONTRACT[code]
    assert status in {400, 404, 422}
    assert message and "{" not in message
    error = main.route_recommendation_error(code)
    assert error.status_code == status
    assert error.detail == {"code": code, "message": message}


@pytest.mark.parametrize("overrides", [
    {"origin": "PORT-CNSHG", "destination": "PORT-CNSHG"},
    {"strategy": "custom"},
    {"limit": 0},
    {"unexpected": True},
])
def test_other_request_validation_remains_standard_422(client, overrides):
    response = client.post("/api/routes/recommend", json=request_body(**overrides))
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_malformed_json_remains_standard_422(client):
    response = client.post(
        "/api/routes/recommend",
        content=b"{",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_origin_destination_and_no_candidates_are_structured(client, monkeypatch):
    monkeypatch.setattr(main, "route_graph_segments", lambda: [])
    assert_error(client.post("/api/routes/recommend", json=BASE_REQUEST), "origin_not_found")
    monkeypatch.setattr(main, "matching_node_ids", lambda segments, location: {location})
    assert_error(client.post("/api/routes/recommend", json=BASE_REQUEST), "no_candidates")


def test_engine_value_error_is_fixed_500_without_exception_text(client, monkeypatch):
    monkeypatch.setattr(main, "route_graph_segments", lambda: [{}])
    monkeypatch.setattr(main, "matching_node_ids", lambda segments, location: {location})
    monkeypatch.setattr(main, "ensure_unambiguous_location_match", lambda *args: None)
    monkeypatch.setattr(main.RecommendationEngine, "recommend", lambda *args: (_ for _ in ()).throw(ValueError("secret")))
    response = client.post("/api/routes/recommend", json=BASE_REQUEST)
    assert response.status_code == 500
    assert response.json() == {"detail": "Route recommendation could not be completed"}
    assert "secret" not in response.text


def test_openapi_declares_business_and_validation_errors(client):
    operation = client.get("/openapi.json").json()["paths"]["/api/routes/recommend"]["post"]
    assert set(operation["responses"]) >= {"200", "400", "404", "422"}
    schema = operation["responses"]["422"]["content"]["application/json"]["schema"]
    assert schema["oneOf"] == [
        {"$ref": "#/components/schemas/HTTPValidationError"},
        {"$ref": "#/components/schemas/RouteRecommendationErrorResponse"},
    ]
