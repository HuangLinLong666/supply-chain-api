from app.vehicle_network.core import load_rates, load_strategy
from app.vehicle_network.models import RouteLegRecord, RouteRecord, SourceType
from app.vehicle_network.providers.route_estimator import haversine_km
from app.vehicle_network.scoring import calculate_risk, estimate_cost, rank_routes


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
