from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from shapely.geometry import box, mapping

import app.main as main
import scripts.cleanup_synthetic_data as cleanup
from app.provider_risk import build_segment_signals, calculate_provider_risk, database_risk_properties
from app.recommendation.engine import RecommendationEngine
from app.recommendation.models import LocationResponse, RecommendationRequest
from app.vehicle_network.core import load_strategy
from app.vehicle_network.models import RouteGenerateRequest
from app.vehicle_network.services import RouteGenerationService
from scripts.migrate_geospatial_data import build_segment_geometry


NOW = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
SUPPLIER = {
    "id": "SUP-STAGE9",
    "name": "Stage 9 Supplier",
    "riskScore": None,
    "riskStatus": "unavailable",
    "riskDataCompleteness": 0.0,
    "riskProviders": [],
}


def recommendation_request(
    strategy: str = "min_risk",
    *,
    auto_reroute: bool = False,
    limit: int = 5,
) -> RecommendationRequest:
    return RecommendationRequest.model_validate(
        {
            "supplierId": SUPPLIER["id"],
            "origin": "Origin",
            "destination": "Destination",
            "cargo": {
                "type": "finished_vehicle",
                "vehicleType": "electric_vehicle",
                "quantity": 1,
            },
            "strategy": strategy,
            "constraints": {"allowedModes": ["road", "rail", "sea", "air"]},
            "limit": limit,
            "autoReroute": auto_reroute,
        }
    )


def route_segment(
    segment_id: str,
    *,
    from_id: str = "A",
    to_id: str = "B",
    mode: str = "sea",
    distance: float = 1000,
    duration: float = 5,
    risk: float | None = 0.2,
    provider: str | None = "Open-Meteo",
    factor_key: str = "weather",
    missing_factors: list[str] | None = None,
    observed_cost: float | None = None,
    feasibility_status: str = "estimated_routable",
    news_risk_score: float = 0.0,
    news_risk_zones: list[str] | None = None,
) -> dict:
    factor = (
        {
            factor_key: {
                "value": risk,
                "status": "available" if provider else "unavailable",
                "provider": provider,
                "observedAt": NOW.isoformat(),
                "expiresAt": (NOW + timedelta(hours=2)).isoformat(),
                "confidence": 0.8,
                "evidence": [f"evidence:{segment_id}"] if provider else [],
            }
        }
        if risk is not None
        else {}
    )
    row = {
        "segment_id": segment_id,
        "from_id": from_id,
        "to_id": to_id,
        "from_name": f"Node {from_id}",
        "from_city": from_id,
        "from_country": "Testland",
        "from_lat": 10.0,
        "from_lng": 20.0,
        "from_coordinate_source": "stage9 fixture",
        "from_coordinate_status": "reference",
        "from_coordinate_confidence": 0.9,
        "to_name": f"Node {to_id}",
        "to_city": to_id,
        "to_country": "Testland",
        "to_lat": 11.0,
        "to_lng": 21.0,
        "to_coordinate_source": "stage9 fixture",
        "to_coordinate_status": "reference",
        "to_coordinate_confidence": 0.9,
        "mode": mode,
        "canonical_mode": mode,
        "distance_km": distance,
        "time_days": duration,
        "data_status": "estimated",
        "feasibility_status": feasibility_status,
        "risk_score": risk,
        "risk_status": "available" if risk is not None and provider else "unavailable",
        "risk_data_completeness": 1.0 if risk is not None and provider else 0.0,
        "risk_confidence": 0.8 if risk is not None and provider else None,
        "risk_missing_factors": list(missing_factors or []),
        "risk_providers": [provider] if provider else [],
        "risk_breakdown": json.dumps(factor),
        "spatial_exposures": [],
        "news_risk_score": news_risk_score,
        "news_risk_zones": list(news_risk_zones or []),
    }
    if observed_cost is not None:
        row["cost_observation"] = {
            "observation_id": f"cost:{segment_id}",
            "data_status": "observed",
            "amount": observed_cost,
            "currency": "USD",
            "quantity": 1,
            "cargo_type": "finished_vehicle",
            "provider": "Stage 9 Fixture Provider",
            "confidence": 0.9,
        }
    return row


def first_leg_ids(result: dict) -> list[str]:
    return [leg["id"] for leg in result["routes"][0]["legs"]]


def test_01_transpacific_rail_is_never_generated():
    origin = {"labels": ["Port"], "country_code": "CN", "latitude": 31.23, "longitude": 121.47}
    destination = {"labels": ["Port"], "country_code": "US", "latitude": 33.74, "longitude": -118.27}
    modes, rejected = RouteGenerationService()._mode_candidates(
        origin,
        destination,
        RouteGenerateRequest(origin="CN-SHA", destination="US-LAX"),
    )
    assert "rail" not in modes
    assert any(item["mode"] == "rail" and "跨大洋铁路" in item["reason"] for item in rejected)


def test_02_cross_ocean_road_is_invalidated_and_excluded():
    source = route_segment("ROAD-OCEAN", mode="road")
    source.update(from_country="China", to_country="United States")
    locations = {
        "A": {"location_id": "A", "latitude": 31.23, "longitude": 121.47},
        "B": {"location_id": "B", "latitude": 33.74, "longitude": -118.27},
    }
    geometry = build_segment_geometry(
        {**source, "element_id": "road-ocean-element"},
        locations,
        {"geometry_geojson": mapping(box(110, 20, 130, 45))},
        NOW.isoformat(),
        enable_osrm=False,
    )
    source["feasibility_status"] = geometry["feasibility_status"]
    assert geometry["feasibility_status"] == "invalid_cross_ocean"
    assert RecommendationEngine().prepare_segments([source], recommendation_request()) == []


def test_03_providerless_risk_is_removed_before_scoring():
    result = RecommendationEngine().recommend(
        [route_segment("NO-PROVIDER", risk=0.5, provider=None)],
        {"A"},
        {"B"},
        SUPPLIER,
        recommendation_request(),
    )
    route = result["routes"][0]
    assert route["riskScore"] is None
    assert route["scoreBreakdown"]["subScores"]["risk"] is None
    assert "provider_backed_risk" in route["missingData"]


def test_04_weather_change_reorders_recommendations():
    engine = RecommendationEngine()
    high_direct = [
        route_segment("DIRECT", risk=0.9, distance=100, duration=1),
        route_segment("SAFE-1", from_id="A", to_id="C", risk=0.1, distance=200, duration=2),
        route_segment("SAFE-2", from_id="C", to_id="B", risk=0.1, distance=200, duration=2),
    ]
    low_direct = [
        route_segment("DIRECT", risk=0.05, distance=100, duration=1),
        route_segment("SAFE-1", from_id="A", to_id="C", risk=0.6, distance=200, duration=2),
        route_segment("SAFE-2", from_id="C", to_id="B", risk=0.6, distance=200, duration=2),
    ]
    assert first_leg_ids(engine.recommend(high_direct, {"A"}, {"B"}, SUPPLIER, recommendation_request())) == [
        "SAFE-1",
        "SAFE-2",
    ]
    assert first_leg_ids(engine.recommend(low_direct, {"A"}, {"B"}, SUPPLIER, recommendation_request())) == [
        "DIRECT"
    ]


def test_05_gdelt_high_risk_zone_triggers_reroute():
    segments = [
        route_segment(
            "RED-SEA-DIRECT",
            distance=100,
            duration=2,
            risk=0.8,
            provider="GDELT",
            factor_key="geopolitical",
            news_risk_score=0.9,
            news_risk_zones=["red-sea"],
        ),
        route_segment("CAPE-1", from_id="A", to_id="C", distance=700, duration=8, risk=0.2),
        route_segment("CAPE-2", from_id="C", to_id="B", distance=700, duration=8, risk=0.2),
    ]
    result = RecommendationEngine().recommend(
        segments,
        {"A"},
        {"B"},
        SUPPLIER,
        recommendation_request("min_cost", auto_reroute=True),
    )
    assert first_leg_ids(result) == ["CAPE-1", "CAPE-2"]
    assert result["dynamicRouting"] == {
        "rerouted": True,
        "avoidedZones": ["red-sea"],
        "fallbackUsed": False,
    }


def test_06_missing_ais_never_creates_congestion_risk():
    provider_risk = calculate_provider_risk("sea", build_segment_signals("sea"), load_strategy())
    result = RecommendationEngine().recommend(
        [route_segment("NO-AIS", risk=None, provider=None, missing_factors=["port_congestion"])],
        {"A"},
        {"B"},
        SUPPLIER,
        recommendation_request(),
    )
    route = result["routes"][0]
    congestion = next(item for item in route["riskFactors"] if item["key"] == "port_congestion")
    assert provider_risk["score"] is None
    assert congestion["status"] == "unavailable"
    assert congestion["provider"] is None
    assert route["legs"][0]["aisCongestionScore"] is None
    assert route["legs"][0]["aisCongestionStatus"] == "unavailable"


def test_07_valid_ais_congestion_changes_sea_route_ranking():
    def ais_segment(segment_id: str, score: float) -> dict:
        risk = calculate_provider_risk(
            "sea",
            build_segment_signals(
                "sea",
                congestion_score=score,
                congestion_provider="AISStream.io",
                congestion_observed_at=NOW.isoformat(),
                congestion_expires_at=(NOW + timedelta(hours=2)).isoformat(),
                congestion_confidence=0.8,
                congestion_evidence=[f"ais:{segment_id}"],
            ),
            load_strategy(),
        )
        properties = database_risk_properties(risk, NOW)
        row = route_segment(
            segment_id,
            risk=risk["score"],
            provider="AISStream.io",
            factor_key="port_congestion",
        )
        row.update(
            risk_status=risk["status"],
            risk_data_completeness=risk["data_completeness"],
            risk_confidence=risk["confidence"],
            risk_missing_factors=risk["missing_factors"],
            risk_providers=risk["providers"],
            risk_breakdown=properties["risk_breakdown"],
            ais_congestion_score=score,
            ais_congestion_status="available",
            ais_congestion_confidence=0.8,
            ais_congestion_data_completeness=1.0,
            ais_congestion_evidence=[f"ais:{segment_id}"],
            ais_congestion_observed_at=NOW.isoformat(),
            ais_congestion_expires_at=(NOW + timedelta(hours=2)).isoformat(),
        )
        return row

    result = RecommendationEngine().recommend(
        [ais_segment("AIS-LOW", 10), ais_segment("AIS-HIGH", 90)],
        {"A"},
        {"B"},
        SUPPLIER,
        recommendation_request(),
    )
    route = result["routes"][0]
    congestion = next(item for item in route["riskFactors"] if item["key"] == "port_congestion")
    assert first_leg_ids(result) == ["AIS-LOW"]
    assert congestion["status"] == "available"
    assert congestion["provider"] == "AISStream.io"
    assert route["legs"][0]["aisCongestionScore"] == 10


def test_08_all_four_strategies_produce_distinct_rankings():
    segments = [
        route_segment("SAFE", risk=0.05, duration=90, observed_cost=45_000),
        route_segment("CHEAP", risk=0.8, duration=60, observed_cost=1_000),
        route_segment("FAST", risk=0.9, duration=2, observed_cost=30_000),
        route_segment("BALANCED", risk=0.3, duration=20, observed_cost=10_000),
    ]
    engine = RecommendationEngine()
    winners = {
        strategy: first_leg_ids(
            engine.recommend(
                segments,
                {"A"},
                {"B"},
                SUPPLIER,
                recommendation_request(strategy),
            )
        )[0]
        for strategy in ("min_risk", "min_cost", "fastest", "balanced")
    }
    assert winners == {
        "min_risk": "SAFE",
        "min_cost": "CHEAP",
        "fastest": "FAST",
        "balanced": "BALANCED",
    }


def test_09_supplier_cannot_use_unrelated_origin(monkeypatch):
    supplier = {
        **SUPPLIER,
        "shippingOrigins": [
            {"elementId": "SHANGHAI", "id": "Shanghai", "name": "Shanghai", "city": "Shanghai"}
        ],
    }
    unrelated = route_segment("UNRELATED", from_id="SHENZHEN", to_id="HAMBURG")
    unrelated.update(from_name="Shenzhen", from_city="Shenzhen", to_name="Hamburg", to_city="Hamburg")
    monkeypatch.setattr(main, "recommendation_supplier", lambda supplier_id: supplier)
    monkeypatch.setattr(main, "route_graph_segments", lambda: [unrelated])
    request = RecommendationRequest.model_validate(
        {
            "supplierId": SUPPLIER["id"],
            "origin": "Shenzhen",
            "destination": "Hamburg",
            "cargo": {"type": "finished_vehicle", "quantity": 1},
            "strategy": "balanced",
        }
    )
    with pytest.raises(HTTPException) as error:
        main.recommend_routes_post(request)
    assert error.value.status_code == 422
    assert "not linked to supplier" in error.value.detail


def test_10_every_returned_node_has_name_and_coordinate_status():
    segment = route_segment("LOCATION-CONTRACT")
    segment.update(
        from_name=None,
        from_city=None,
        from_coordinate_source=None,
        from_coordinate_status=None,
        to_name=None,
        to_city=None,
        to_coordinate_source=None,
        to_coordinate_status=None,
    )
    result = RecommendationEngine().recommend(
        [segment],
        {"A"},
        {"B"},
        SUPPLIER,
        recommendation_request(),
    )
    leg = result["routes"][0]["legs"][0]
    assert LocationResponse.model_validate(leg["from"]).name == "A"
    assert LocationResponse.model_validate(leg["to"]).name == "B"
    assert leg["from"]["coordinateStatus"] == "unavailable"
    assert leg["to"]["coordinateStatus"] == "unavailable"


def test_11_every_risk_factor_has_status_and_valid_source_semantics():
    result = RecommendationEngine().recommend(
        [route_segment("RISK-METADATA", risk=0.25, missing_factors=["piracy"])],
        {"A"},
        {"B"},
        SUPPLIER,
        recommendation_request(),
    )
    factors = result["routes"][0]["riskFactors"]
    assert factors
    assert all(factor["status"] in {"available", "partial", "unavailable"} for factor in factors)
    assert all(factor["provider"] for factor in factors if factor["status"] in {"available", "partial"})
    piracy = next(factor for factor in factors if factor["key"] == "piracy")
    assert piracy["score"] is None
    assert piracy["provider"] is None
    assert piracy["status"] == "unavailable"


def test_12_cleanup_reexecution_is_idempotent_and_preserves_realtime(monkeypatch):
    class DeleteResult:
        def __init__(self, deleted: int):
            self.deleted = deleted

        def single(self):
            return {"deleted": self.deleted}

    class FakeTransaction:
        def __init__(self):
            self.nodes = {"synthetic", "realtime"}

        def run(self, query, **parameters):
            if "DETACH DELETE" in query:
                deleted = 0
                for element_id in parameters["element_ids"]:
                    if element_id in self.nodes:
                        self.nodes.remove(element_id)
                        deleted += 1
                return DeleteResult(deleted)
            return [{"label": "NewsRiskEvent", "count": int("realtime" in self.nodes)}]

    class FakeSession:
        def __init__(self, transaction):
            self.transaction = transaction

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute_write(self, callback):
            return callback(self.transaction)

    class FakeDriver:
        def __init__(self, transaction):
            self.transaction = transaction

        def session(self, **options):
            return FakeSession(self.transaction)

    transaction = FakeTransaction()
    monkeypatch.setattr(cleanup, "get_driver", lambda: FakeDriver(transaction))
    repository = cleanup.CleanupRepository.__new__(cleanup.CleanupRepository)
    repository.settings = SimpleNamespace(database=None)
    baseline = {"NewsRiskEvent": 1}
    assert repository.delete_nodes(["synthetic"], baseline) == 1
    assert repository.delete_nodes(["synthetic"], baseline) == 0
    assert transaction.nodes == {"realtime"}


def test_13_realtime_import_writers_use_stable_merge_identities():
    expected = {
        "ais/repository.py": "MERGE (vessel:Vessel {mmsi:row.mmsi})",
        "gdelt/repository.py": "MERGE (e:NewsRiskEvent {article_id:row.article_id})",
        "weather/repository.py": "MERGE (w:WeatherRiskSnapshot {snapshot_id:$snapshot_id})",
        "app/vehicle_network/repository.py": "MERGE (route:VehicleRoute:Route {route_id:$route_id})",
    }
    for filename, identity_merge in expected.items():
        assert identity_merge in Path(filename).read_text(encoding="utf-8")


def test_14_legacy_recommendation_api_remains_available():
    operation = main.app.openapi()["paths"]["/api/routes/recommend"]["get"]
    assert operation["deprecated"] is True
    assert operation["responses"]["200"]


def test_15_post_recommendation_api_matches_openapi_models():
    operation = main.app.openapi()["paths"]["/api/routes/recommend"]["post"]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert request_schema.endswith("RecommendationRequest")
    assert response_schema.endswith("RecommendationResponse")
    assert "/api/routes/{route_id}" in main.app.openapi()["paths"]
