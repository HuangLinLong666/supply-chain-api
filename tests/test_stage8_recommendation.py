from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import app.main as main
from app.recommendation.engine import RecommendationEngine
from app.recommendation.models import RecommendationRequest, RecommendationResponse
from app.recommendation.storage import persist_recommendation_snapshot


def recommendation_request(strategy: str = "balanced", **overrides):
    payload = {
        "supplierId": "SUP-CATL",
        "origin": "Shanghai",
        "destination": "Hamburg",
        "cargo": {"type": "finished_vehicle", "vehicleType": "electric_vehicle", "quantity": 1},
        "strategy": strategy,
        "constraints": {"allowedModes": ["road", "rail", "sea", "air"]},
        "limit": 5,
        "autoReroute": False,
    }
    payload.update(overrides)
    return RecommendationRequest.model_validate(payload)


def segment(segment_id: str, mode: str, distance: float, duration: float, risk: float | None):
    provider = "Open-Meteo" if risk is not None else None
    return {
        "segment_id": segment_id,
        "from_id": "A",
        "to_id": "B",
        "from_name": "上海港",
        "from_city": "Shanghai",
        "from_country": "China",
        "to_name": "汉堡港",
        "to_city": "Hamburg",
        "to_country": "Germany",
        "mode": mode,
        "canonical_mode": mode,
        "distance_km": distance,
        "time_days": duration,
        "data_status": "estimated",
        "feasibility_status": "estimated_routable",
        "risk_score": risk,
        "risk_status": "available" if risk is not None else "unavailable",
        "risk_data_completeness": 1.0 if risk is not None else 0.0,
        "risk_confidence": 0.9 if risk is not None else None,
        "risk_missing_factors": [] if risk is not None else ["war", "natural_disaster", "trade_policy"],
        "risk_providers": [provider] if provider else [],
        "risk_breakdown": json.dumps(
            {"natural_disaster": {"value": risk, "provider": provider, "evidence": [f"evidence-{segment_id}"]}}
            if risk is not None
            else {}
        ),
        "spatial_exposures": [],
        "route_weather_risk": risk,
        "route_weather_provider": provider,
        "route_weather_confidence": 0.9 if risk is not None else None,
        "route_weather_evidence": [f"evidence-{segment_id}"] if risk is not None else [],
    }


def supplier_without_risk():
    return {
        "id": "SUP-CATL",
        "name": "CATL",
        "riskScore": None,
        "riskStatus": "unavailable",
        "riskDataCompleteness": 0.0,
        "riskProviders": [],
    }


def test_weight_sum_must_equal_one():
    with pytest.raises(ValidationError, match="权重之和必须等于 1"):
        recommendation_request(
            "custom",
            weights={"risk": 0.5, "cost": 0.4, "duration": 0.2},
        )


def test_fixed_normalization_does_not_depend_on_candidate_maximum():
    engine = RecommendationEngine()
    first = engine._utility_score(10_000, "cost_per_vehicle_usd")
    engine.recommend(
        [segment("EXPENSIVE", "air", 10_000, 2.0, 0.2)],
        {"A"},
        {"B"},
        supplier_without_risk(),
        recommendation_request(),
    )
    assert engine._utility_score(10_000, "cost_per_vehicle_usd") == first == 80.0


def test_cost_fallback_uses_quantity_loading_method_weight_and_dimensions():
    engine = RecommendationEngine()
    request = recommendation_request(
        cargo={
            "type": "finished_vehicle",
            "vehicleType": "electric_vehicle",
            "quantity": 20,
            "grossWeightKg": 44_000,
            "vehicleLengthM": 4.8,
            "vehicleWidthM": 1.9,
            "vehicleHeightM": 1.7,
            "shipmentMethod": "roro",
        }
    )
    prepared = engine.prepare_segments([segment("SEA", "sea", 1_000, 5.0, 0.2)], request)[0]
    snapshot = prepared["_cost_estimate"]["input_snapshot"]
    assert snapshot["quantity"] == 20
    assert snapshot["shipmentMethod"] == "roro"
    assert snapshot["unitWeightKg"] == 2_200
    assert snapshot["vehicleVolumeM3"] == pytest.approx(15.504)
    assert snapshot["shipmentMultiplier"] == 0.92
    assert prepared["_cost_estimate"]["data_status"] == "estimated"
    assert prepared["_cost_estimate"]["provider"] is None


def test_min_risk_min_cost_and_fastest_choose_different_routes():
    segments = [
        segment("LOW-RISK-AIR", "air", 1_000, 0.5, 0.10),
        segment("LOW-COST-SEA", "sea", 100, 10.0, 0.80),
        segment("FAST-ROAD", "road", 400, 0.1, 0.40),
    ]
    engine = RecommendationEngine()
    winners = {}
    for strategy in ("min_risk", "min_cost", "fastest"):
        result = engine.recommend(
            segments,
            {"A"},
            {"B"},
            supplier_without_risk(),
            recommendation_request(strategy),
        )
        winners[strategy] = result["routes"][0]["legs"][0]["id"]
    assert winners == {
        "min_risk": "LOW-RISK-AIR",
        "min_cost": "LOW-COST-SEA",
        "fastest": "FAST-ROAD",
    }


def test_hard_constraints_filter_before_ranking():
    engine = RecommendationEngine()
    request = recommendation_request(
        "min_risk",
        constraints={"allowedModes": ["sea", "air"], "maxCostUsd": 1_000},
    )
    result = engine.recommend(
        [
            segment("LOW-RISK-AIR", "air", 1_000, 0.5, 0.05),
            segment("ELIGIBLE-SEA", "sea", 100, 8.0, 0.60),
        ],
        {"A"},
        {"B"},
        supplier_without_risk(),
        request,
    )
    assert result["routes"][0]["legs"][0]["id"] == "ELIGIBLE-SEA"
    assert any(item["routeId"].startswith("route-") and "cost" in item["reasons"][0] for item in result["rejectedCandidates"])


def test_unknown_risk_is_null_and_penalized_separately():
    engine = RecommendationEngine()
    result = engine.recommend(
        [segment("UNKNOWN-RISK", "sea", 100, 3.0, None)],
        {"A"},
        {"B"},
        supplier_without_risk(),
        recommendation_request("balanced"),
    )
    route = result["routes"][0]
    assert route["riskScore"] is None
    assert route["scoreBreakdown"]["subScores"]["risk"] is None
    assert route["uncertaintyPenalty"] > 0
    assert any("未填入 50 分" in explanation for explanation in route["whyRecommended"])


def test_unknown_risk_cannot_silently_pass_max_risk_constraint():
    engine = RecommendationEngine()
    request = recommendation_request(
        "balanced",
        constraints={"allowedModes": ["sea"], "maxRiskScore": 70},
    )
    result = engine.recommend(
        [segment("UNKNOWN-RISK", "sea", 100, 3.0, None)],
        {"A"},
        {"B"},
        supplier_without_risk(),
        request,
    )
    assert result["routes"] == []
    assert "无法验证" in result["rejectedCandidates"][0]["reasons"][0]


def test_supplier_origin_filter_rejects_unrelated_city_nodes():
    segments = [
        {
            "from_id": "SHANGHAI-NODE",
            "from_name": "上海港",
            "from_city": "Shanghai",
            "from_location_id": "CN-SHA",
            "to_id": "HAMBURG",
            "to_name": "汉堡港",
            "to_city": "Hamburg",
        },
        {
            "from_id": "SHENZHEN-NODE",
            "from_name": "盐田港",
            "from_city": "Shenzhen",
            "from_location_id": "CN-SZX",
            "to_id": "HAMBURG",
            "to_name": "汉堡港",
            "to_city": "Hamburg",
        },
    ]
    supplier = {"shippingOrigins": [{"elementId": "ORIGIN", "id": "Shanghai", "name": "Shanghai", "city": "Shanghai"}]}
    assert main.supplier_origin_node_ids(
        segments,
        {"SHANGHAI-NODE", "SHENZHEN-NODE"},
        supplier,
    ) == {"SHANGHAI-NODE"}


def test_city_match_with_multiple_location_ids_is_rejected():
    segments = [
        {
            "from_id": "PORT-NODE",
            "from_name": "Shanghai Port",
            "from_city": "Shanghai",
            "from_location_id": "PORT-CNSHG",
            "from_labels": ["Port", "TransportLocation"],
            "to_id": "DESTINATION",
            "to_location_id": "AIR-FRA",
        },
        {
            "from_id": "AIRPORT-NODE",
            "from_name": "Shanghai Pudong International Airport",
            "from_city": "Shanghai",
            "from_location_id": "AIR-PVG",
            "from_labels": ["Airport", "TransportLocation"],
            "to_id": "DESTINATION",
            "to_location_id": "AIR-FRA",
        },
    ]
    matches = main.matching_node_ids(segments, "Shanghai")
    with pytest.raises(main.HTTPException) as error:
        main.ensure_unambiguous_location_match("origin", "Shanghai", segments, matches)
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "ambiguous_location"
    assert {item["locationId"] for item in error.value.detail["matches"]} == {"PORT-CNSHG", "AIR-PVG"}


def test_exact_airport_location_id_is_not_expanded_to_same_city_port():
    segments = [
        {
            "from_id": "PORT-NODE",
            "from_city": "Shanghai",
            "from_location_id": "PORT-CNSHG",
            "to_id": "DESTINATION",
        },
        {
            "from_id": "AIRPORT-NODE",
            "from_city": "Shanghai",
            "from_location_id": "AIR-PVG",
            "to_id": "DESTINATION",
        },
    ]
    assert main.matching_node_ids(segments, "AIR-PVG") == {"AIRPORT-NODE"}


def test_mode_must_match_endpoint_location_types():
    invalid_sea = segment("INVALID-SEA", "sea", 100, 3.0, 0.2)
    invalid_sea.update(from_labels=["Airport", "TransportLocation"], to_labels=["Airport", "TransportLocation"])
    valid_air = segment("VALID-AIR", "air", 100, 1.0, 0.2)
    valid_air.update(from_labels=["Airport", "TransportLocation"], to_labels=["Airport", "TransportLocation"])
    prepared = RecommendationEngine().prepare_segments([invalid_sea, valid_air], recommendation_request())
    assert [item["segment_id"] for item in prepared] == ["VALID-AIR"]


def test_required_modes_are_enforced_during_candidate_generation_and_constraints():
    road = segment("ROAD-A-M", "road", 100, 1.0, 0.2)
    road.update(to_id="M", to_name="中转港")
    sea = segment("SEA-M-B", "sea", 500, 5.0, 0.2)
    sea.update(from_id="M", from_name="中转港")
    sea_only = segment("SEA-A-B", "sea", 450, 4.0, 0.2)
    request = recommendation_request(
        constraints={"allowedModes": ["road", "sea"], "requiredModes": ["road", "sea"]}
    )

    result = RecommendationEngine().recommend(
        [road, sea, sea_only],
        {"A"},
        {"B"},
        supplier_without_risk(),
        request,
    )

    assert result["routes"]
    assert all({leg["mode"] for leg in route["legs"]} == {"road", "sea"} for route in result["routes"])


def test_required_modes_must_be_allowed():
    with pytest.raises(ValidationError, match="requiredModes"):
        recommendation_request(
            constraints={"allowedModes": ["sea"], "requiredModes": ["road", "sea"]}
        )


def test_route_recommendation_token_is_required_when_configured(monkeypatch):
    monkeypatch.setenv("ROUTE_RECOMMENDATION_TOKEN", "route-token-test")
    with pytest.raises(main.HTTPException) as error:
        main.require_route_recommendation_token(None)
    assert error.value.status_code == 401
    main.require_route_recommendation_token("route-token-test")


def test_production_fails_closed_when_route_recommendation_token_is_missing(monkeypatch):
    monkeypatch.delenv("ROUTE_RECOMMENDATION_TOKEN", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(main.HTTPException) as error:
        main.require_route_recommendation_token(None)
    assert error.value.status_code == 503


def test_authenticated_gdelt_monitor_update_can_wait_for_fresh_snapshot(monkeypatch):
    monkeypatch.setattr(main, "GdeltSettings", lambda: SimpleNamespace(admin_token="gdelt-test"))
    monkeypatch.setattr(
        main,
        "update_news_risk",
        lambda dry_run, zone_ids=None: {
            "updatedAt": "2026-09-06T00:00:00Z",
            "zonesUpdated": 1,
            "segmentsExposed": 2,
            "failures": [],
        },
    )
    result = main.trigger_gdelt_update(
        main.GdeltUpdateRequest(waitForCompletion=True),
        main.BackgroundTasks(),
        "gdelt-test",
    )
    assert result == {
        "status": "completed",
        "updatedAt": "2026-09-06T00:00:00Z",
        "zonesUpdated": 1,
        "segmentsExposed": 2,
        "failureCount": 0,
    }


def test_openapi_has_new_post_and_deprecated_get_contracts():
    operation = main.app.openapi()["paths"]["/api/routes/recommend"]
    assert operation["get"]["deprecated"] is True
    assert "requestBody" in operation["post"]
    assert operation["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("RecommendationResponse")
    assert "/api/recommendations/{snapshot_id}" in main.app.openapi()["paths"]
    assert "/api/routes/{route_id}" in main.app.openapi()["paths"]


def test_snapshot_persists_full_input_and_segment_links():
    engine = RecommendationEngine()
    request = recommendation_request()
    result = engine.recommend(
        [segment("SEG-1", "sea", 100, 3.0, 0.2)],
        {"A"},
        {"B"},
        supplier_without_risk(),
        request,
    )
    response = RecommendationResponse.model_validate(
        {
            "snapshotId": "recommendation_test",
            "scoringVersion": engine.scoring_version,
            "generatedAt": "2026-07-26T00:00:00Z",
            "query": {},
            "resolvedWeights": engine.resolved_weights(request).model_dump(),
            "normalization": engine.normalization_metadata(),
            "dynamicRouting": result["dynamicRouting"],
            "candidateCount": result["candidateCount"],
            "eligibleCount": result["eligibleCount"],
            "count": len(result["routes"]),
            "rejectedCandidates": result["rejectedCandidates"],
            "routes": result["routes"],
        }
    )
    calls = []

    def query(cypher, parameters=None):
        calls.append((cypher, parameters or {}))
        return []

    persist_recommendation_snapshot(query, request, response, result["includedSegments"], 30)
    assert len(calls) == 2
    assert json.loads(calls[0][1]["input_snapshot_json"])["supplierId"] == "SUP-CATL"
    assert calls[1][1]["inclusions"] == [{"segment_id": "SEG-1", "route_ids": [result["routes"][0]["id"]]}]
