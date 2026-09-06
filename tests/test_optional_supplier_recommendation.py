from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.main as main
from app.recommendation.engine import RecommendationEngine
from app.recommendation.models import RecommendationRequest
from app.recommendation.storage import persist_recommendation_snapshot
from tests.test_stage9_acceptance import route_segment


def request(**overrides) -> RecommendationRequest:
    payload = {
        "origin": "A",
        "destination": "B",
        "cargo": {"type": "finished_vehicle", "quantity": 1},
        "strategy": "balanced",
        "constraints": {"allowedModes": ["road", "sea"]},
        "autoReroute": True,
        "limit": 5,
    }
    payload.update(overrides)
    return RecommendationRequest.model_validate(payload)


def install_endpoint_fixtures(monkeypatch, segments, supplier_lookup=None):
    monkeypatch.setattr(main, "route_graph_segments", lambda: segments)
    monkeypatch.setattr(main, "persist_recommendation_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "load_recommendation_settings", lambda: {"snapshot": {"retention_days": 30}})
    if supplier_lookup is not None:
        monkeypatch.setattr(main, "recommendation_supplier", supplier_lookup)


def valid_supplier(**overrides):
    value = {
        "id": "SUP-REAL",
        "name": "Real Supplier",
        "city": "Origin",
        "country": "CN",
        "shippingOrigins": [{"elementId": "A", "id": "Origin", "name": "Origin", "city": "Origin"}],
        "riskScore": 0.8,
        "riskStatus": "available",
        "riskDataCompleteness": 1.0,
        "riskConfidence": 0.9,
        "riskProviders": ["provider-a"],
        "riskEvidence": ["supplier-evidence-1"],
        "riskObservedAt": "2026-09-06T00:00:00Z",
        "riskExpiresAt": "2999-09-06T03:00:00Z",
        "riskExplanation": "verified supplier risk",
    }
    value.update(overrides)
    return value


def test_supplier_id_is_optional_but_whitespace_is_rejected():
    assert request().supplier_id is None
    with pytest.raises(ValidationError, match="supplierId 提供时不能为空"):
        request(supplierId="   ")

    openapi = main.app.openapi()
    request_schema = openapi["components"]["schemas"]["RecommendationRequest"]
    response_schema = openapi["components"]["schemas"]["RecommendationResponse"]
    assert "supplierId" not in request_schema["required"]
    assert "supplierContext" in response_schema["properties"]


def test_missing_supplier_skips_lookup_and_returns_not_provided_context(monkeypatch):
    segment = route_segment("NO-SUPPLIER", risk=None, provider=None)

    def forbidden_lookup(_value):
        raise AssertionError("supplier lookup must not run")

    install_endpoint_fixtures(monkeypatch, [segment], forbidden_lookup)
    response = main.recommend_routes_post(request())
    payload = response.model_dump(mode="json", by_alias=True)

    assert payload["supplierContext"] == {
        "status": "not_provided",
        "supplierId": None,
        "riskStatus": "unavailable",
    }
    assert payload["query"]["supplier"] is None
    assert payload["routes"][0]["riskScore"] is None
    assert all(factor["key"] != "supplier" for factor in payload["routes"][0]["riskFactors"])


def test_missing_supplier_does_not_relax_origin_ambiguity(monkeypatch):
    port = route_segment("PORT", from_id="PORT-A")
    port.update(from_location_id="PORT-CNAAA", from_name="Origin Port", from_city="Origin")
    airport = route_segment("AIR", from_id="AIR-A", mode="road")
    airport.update(from_location_id="AIR-CNAAA", from_name="Origin Airport", from_city="Origin")
    install_endpoint_fixtures(monkeypatch, [port, airport], lambda _value: None)

    with pytest.raises(HTTPException) as error:
        main.recommend_routes_post(request(origin="Origin"))
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "ambiguous_location"


def test_four_strategies_keep_distinct_rankings_without_supplier():
    segments = [
        route_segment("SAFE", risk=0.05, duration=90, observed_cost=45_000),
        route_segment("CHEAP", risk=0.8, duration=60, observed_cost=1_000),
        route_segment("FAST", risk=0.9, duration=2, observed_cost=30_000),
        route_segment("BALANCED", risk=0.3, duration=20, observed_cost=10_000),
    ]
    winners = {}
    for strategy in ("min_risk", "min_cost", "fastest", "balanced"):
        result = RecommendationEngine().recommend(
            segments,
            {"A"},
            {"B"},
            None,
                request(strategy=strategy, autoReroute=False, constraints={"allowedModes": ["sea"]}),
        )
        winners[strategy] = result["routes"][0]["legs"][0]["id"]
    assert winners == {"min_risk": "SAFE", "min_cost": "CHEAP", "fastest": "FAST", "balanced": "BALANCED"}


@pytest.mark.parametrize(
    ("allowed_modes", "segments", "expected_modes"),
    [
        (["road"], [route_segment("ROAD", mode="road")], {"road"}),
        (["sea"], [route_segment("SEA", mode="sea")], {"sea"}),
    ],
)
def test_single_mode_constraints_work_without_supplier(allowed_modes, segments, expected_modes):
    result = RecommendationEngine().recommend(
        segments,
        {"A"},
        {"B"},
        None,
        request(constraints={"allowedModes": allowed_modes}),
    )
    assert result["routes"]
    assert all({leg["mode"] for leg in route["legs"]} == expected_modes for route in result["routes"])


def test_required_road_and_sea_modes_work_without_supplier():
    road = route_segment("ROAD-A-M", mode="road", from_id="A", to_id="M")
    sea = route_segment("SEA-M-B", mode="sea", from_id="M", to_id="B")
    result = RecommendationEngine().recommend(
        [road, sea],
        {"A"},
        {"B"},
        None,
        request(constraints={"allowedModes": ["road", "sea"], "requiredModes": ["road", "sea"]}),
    )
    assert result["routes"]
    assert all({leg["mode"] for leg in route["legs"]} == {"road", "sea"} for route in result["routes"])


def test_dynamic_reroute_works_without_supplier():
    risky = route_segment(
        "HORMUZ-SEA",
        risk=0.95,
        provider="GDELT",
        news_risk_score=0.95,
        news_risk_zones=["hormuz-strait"],
    )
    alternative = route_segment("ROAD-ALT", mode="road", distance=900, duration=8, risk=0.1)
    result = RecommendationEngine().recommend(
        [risky, alternative],
        {"A"},
        {"B"},
        None,
        request(strategy="min_cost"),
    )
    assert result["routes"][0]["legs"][0]["id"] == "ROAD-ALT"
    assert result["dynamicRouting"]["rerouted"] is True
    assert result["dynamicRouting"]["avoidedZones"] == ["hormuz-strait"]


def test_valid_supplier_keeps_origin_check_and_active_risk_scoring(monkeypatch):
    supplier = valid_supplier()
    install_endpoint_fixtures(monkeypatch, [route_segment("SUPPLIER-RISK", risk=0.2)], lambda _value: supplier)
    response = main.recommend_routes_post(request(supplierId=supplier["id"]))
    payload = response.model_dump(mode="json", by_alias=True)
    supplier_factor = next(factor for factor in payload["routes"][0]["riskFactors"] if factor["key"] == "supplier")

    assert payload["supplierContext"] == {
        "status": "resolved",
        "supplierId": "SUP-REAL",
        "riskStatus": "available",
    }
    assert supplier_factor["score"] == 80
    assert supplier_factor["provider"] == "provider-a"
    assert supplier_factor["expiresAt"] == "2999-09-06T03:00:00Z"


@pytest.mark.parametrize(
    "risk_overrides",
    [
        {"riskScore": None, "riskProviders": [], "riskEvidence": [], "riskStatus": "unavailable"},
        {"riskExpiresAt": "2000-01-01T00:00:00Z"},
    ],
)
def test_missing_or_expired_supplier_risk_is_unavailable_and_does_not_change_route_score(monkeypatch, risk_overrides):
    segment = route_segment("EXPIRED-SUPPLIER", risk=0.2)
    baseline = RecommendationEngine().recommend([segment], {"A"}, {"B"}, None, request())["routes"][0]
    supplier = valid_supplier(**risk_overrides)
    install_endpoint_fixtures(monkeypatch, [segment], lambda _value: supplier)
    payload = main.recommend_routes_post(request(supplierId=supplier["id"])).model_dump(mode="json", by_alias=True)

    assert payload["supplierContext"]["riskStatus"] == "unavailable"
    assert payload["routes"][0]["riskScore"] == baseline["riskScore"]
    assert all(factor["key"] != "supplier" for factor in payload["routes"][0]["riskFactors"])


def test_structured_supplier_not_found_and_origin_errors(monkeypatch):
    install_endpoint_fixtures(monkeypatch, [route_segment("ERRORS")], lambda _value: None)
    with pytest.raises(HTTPException) as missing:
        main.recommend_routes_post(request(supplierId="SUP-MISSING"))
    assert missing.value.status_code == 404
    assert missing.value.detail["code"] == "supplier_not_found"

    supplier = valid_supplier(shippingOrigins=[])
    monkeypatch.setattr(main, "recommendation_supplier", lambda _value: supplier)
    with pytest.raises(HTTPException) as unmapped:
        main.recommend_routes_post(request(supplierId=supplier["id"]))
    assert unmapped.value.status_code == 422
    assert unmapped.value.detail["code"] == "supplier_origin_unmapped"

    supplier = valid_supplier(shippingOrigins=[{"elementId": "OTHER", "id": "Other", "name": "Other"}])
    monkeypatch.setattr(main, "recommendation_supplier", lambda _value: supplier)
    with pytest.raises(HTTPException) as mismatch:
        main.recommend_routes_post(request(supplierId=supplier["id"]))
    assert mismatch.value.status_code == 422
    assert mismatch.value.detail["code"] == "supplier_origin_mismatch"


def test_ambiguous_supplier_lookup_is_not_arbitrarily_selected(monkeypatch):
    monkeypatch.setattr(
        main,
        "safe_query",
        lambda _query, _parameters=None: [
            {"id": "SUP-A", "name": "Same", "exactRank": 1},
            {"id": "SUP-B", "name": "Same", "exactRank": 1},
        ],
    )
    with pytest.raises(HTTPException) as error:
        main.recommendation_supplier("Same")
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "supplier_ambiguous"


def test_snapshot_persists_null_supplier_without_placeholder():
    response = main.RecommendationResponse.model_validate(
        {
            "snapshotId": "optional-supplier-snapshot",
            "scoringVersion": "test",
            "generatedAt": "2026-09-06T00:00:00Z",
            "supplierContext": {"status": "not_provided", "riskStatus": "unavailable"},
            "query": {},
            "resolvedWeights": {"risk": 0.4, "cost": 0.3, "duration": 0.3},
            "normalization": RecommendationEngine().normalization_metadata(),
            "dynamicRouting": {},
            "candidateCount": 0,
            "eligibleCount": 0,
            "count": 0,
            "routes": [],
        }
    )
    calls = []
    persist_recommendation_snapshot(lambda query, parameters=None: calls.append((query, parameters)) or [], request(), response, [], 30)
    assert calls[0][1]["supplier_id"] is None
    saved = json.loads(calls[0][1]["input_snapshot_json"])
    assert saved["supplierId"] is None
    assert "UNKNOWN_SUPPLIER" not in json.dumps(saved)
