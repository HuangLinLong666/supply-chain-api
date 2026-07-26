from app.route_optimizer import format_route, risk_optimization_value, segment_weight, shortest_path


def route_segment(**overrides):
    row = {
        "segment_id": "SEG-1",
        "from_id": "A",
        "to_id": "B",
        "mode": "sea",
        "cost_usd": 100,
        "cost_score": 0.2,
        "time_days": 2,
        "distance_km": 1000,
        "risk_score": None,
        "risk_status": "unavailable",
        "risk_data_completeness": 0.0,
        "risk_missing_factors": ["weather"],
        "risk_providers": [],
    }
    row.update(overrides)
    return row


def test_missing_risk_uses_explicit_worst_case_ranking_penalty():
    segment = route_segment()
    assert risk_optimization_value(segment) == 1.0
    assert segment_weight(segment, "min_risk", 0.5) == 1.0


def test_partial_risk_adds_completeness_penalty_without_changing_reported_score():
    segment = route_segment(
        risk_score=0.4,
        risk_status="partial",
        risk_data_completeness=0.2,
        risk_providers=["Open-Meteo"],
    )
    assert risk_optimization_value(segment) == 0.6
    route = format_route([segment], 1)
    assert route["riskScore"] == 40
    assert route["riskDataCompleteness"] == 0.2
    assert route["riskStatus"] == "partial"


def test_unavailable_path_returns_null_risk_not_fifty():
    segment = route_segment()
    route = format_route([segment], 1)
    optimized = shortest_path([segment], "A", "B", "min_risk", 1.0)
    assert route["riskScore"] is None
    assert route["legs"][0]["riskScore"] is None
    assert optimized["average_risk_score"] is None
    assert optimized["risk_status"] == "unavailable"
