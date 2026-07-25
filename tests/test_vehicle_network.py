from app.vehicle_network.core import load_rates, load_strategy
from app.vehicle_network.models import LocationKind, LocationRecord, RouteLegRecord, RouteRecord, SourceType
from app.vehicle_network.providers.route_estimator import haversine_km
from app.vehicle_network.repository import VehicleNetworkRepository
from app.vehicle_network.scoring import calculate_risk, estimate_cost, rank_routes
from app.vehicle_network.scoring import calculate_mode_risk
from app.vehicle_network.models import RouteGenerateRequest
from app.vehicle_network.services import RouteGenerationService


PROVENANCE = {
    "source": "测试数据", "source_type": SourceType.FABRICATED_FOR_TESTING,
    "confidence": 0.7, "is_inferred": True,
}


def test_haversine_distance_is_reasonable():
    distance = haversine_km({"latitude": 31.23, "longitude": 121.47}, {"latitude": 33.75, "longitude": -118.22})
    assert 10000 < distance < 11000


def test_cost_range_keeps_formula_snapshot():
    leg = RouteLegRecord(**PROVENANCE, leg_id="leg_test", sequence=1, mode="sea", origin_id="A", destination_id="B", distance_km=10000, duration_h=300)
    cost = estimate_cost([leg], load_rates())
    assert cost.min < cost.most_likely < cost.max
    assert cost.input_snapshot["legs"][0]["mode"] == "sea"


def test_risk_weighting_and_level():
    risk = calculate_risk({"news": 90, "weather": 70, "congestion": 60, "sanctions": 90, "schedule_reliability": 50}, load_strategy())
    assert risk.risk_score >= 60
    assert risk.risk_level in {"high", "critical"}
    assert len(risk.risk_factors) == 5


def test_hybrid_ranking_prefers_safer_route_when_other_values_equal():
    strategy = load_strategy()
    routes = []
    for identifier, news in (("safe", 10), ("risky", 90)):
        leg = RouteLegRecord(**PROVENANCE, leg_id=f"leg_{identifier}", sequence=1, mode="sea", origin_id="A", destination_id="B", distance_km=1000, duration_h=100)
        route = RouteRecord(**PROVENANCE, route_id=identifier, route_type="sea", origin_id="A", destination_id="B", legs_count=1, estimated_distance_km=1000, estimated_duration_h=100, legs=[leg])
        route.estimated_cost = estimate_cost([leg], load_rates())
        route.risk = calculate_risk({"news": news, "weather": 20, "congestion": 20, "sanctions": 10, "schedule_reliability": 20}, strategy)
        routes.append(route)
    ranked = rank_routes(routes, "hybrid", strategy)
    assert ranked[0].route_id == "safe"


def test_existing_unlocode_reuses_node_instead_of_creating_duplicate():
    class FakeResult(list):
        def consume(self):
            return None

    class FakeTransaction:
        def __init__(self):
            self.queries = []

        def run(self, query, **parameters):
            self.queries.append(query)
            if "RETURN elementId(location) AS element_id" in query:
                return FakeResult([{"element_id": "existing-port"}])
            return FakeResult()

    class FakeRepository(VehicleNetworkRepository):
        def __init__(self):
            self.transaction = FakeTransaction()

        def _execute_write(self, callback):
            return callback(self.transaction)

    location = LocationRecord(
        source="测试", source_type=SourceType.FABRICATED_FOR_TESTING, confidence=0.2,
        is_inferred=True, id="CN-SHA", kind=LocationKind.PORT, name_en="Shanghai Port",
        country_code="CN", unlocode="CNSHA", latitude=31.23, longitude=121.47,
    )
    repository = FakeRepository()
    assert repository.merge_locations([location], "job-test") == 1
    assert any("elementId(location)=$element_id" in query for query in repository.transaction.queries)
    assert not any("MERGE (location:TransportLocation:Port {location_id:$id})" in query for query in repository.transaction.queries)


def test_transpacific_port_route_rejects_rail_and_road():
    service = RouteGenerationService()
    origin = {"labels": ["Port"], "country_code": "CN", "latitude": 31.23, "longitude": 121.47}
    destination = {"labels": ["Port"], "country_code": "US", "latitude": 33.74, "longitude": -118.27}
    modes, rejected = service._mode_candidates(origin, destination, RouteGenerateRequest(origin="CN-SHA", destination="US-LAX"))
    assert modes == ["sea"]
    assert {item["mode"] for item in rejected} == {"rail", "road"}


def test_sea_and_rail_use_different_risk_factors():
    strategy = load_strategy()
    sea = calculate_mode_risk("sea", {"weather": 50, "piracy": 80, "port_congestion": 40, "geopolitical": 30, "sanctions": 10, "schedule_reliability": 35}, strategy)
    rail = calculate_mode_risk("rail", {"border_customs": 60, "geopolitical": 30, "infrastructure": 20, "weather": 50, "schedule_reliability": 35, "sanctions": 10}, strategy)
    assert any("海盗" in factor for factor in sea.risk_factors)
    assert any("边境与海关" in factor for factor in rail.risk_factors)
    assert sea.risk_factors != rail.risk_factors


def test_missing_provider_signals_do_not_join_risk_calculation():
    strategy = load_strategy()
    risk = calculate_mode_risk(
        "sea",
        {"weather": 40, "piracy": None, "port_congestion": 60, "geopolitical": None, "sanctions": None, "schedule_reliability": None},
        strategy,
    )
    expected = (40 * 0.22 + 60 * 0.20) / (0.22 + 0.20)
    assert risk.risk_score == round(expected, 2)
    assert all("海盗" not in factor for factor in risk.risk_factors)
    assert "海盗与海上安全" in risk.missing_factors
    assert risk.data_completeness == 0.42


def test_all_missing_signals_return_unknown_not_low_risk():
    risk = calculate_mode_risk("rail", {}, load_strategy())
    assert risk.risk_score is None
    assert risk.risk_level == "unknown"
    assert risk.data_completeness == 0
