from __future__ import annotations

from typing import Any

from app.vehicle_network.models import CostRange, RiskResult, RouteRecord


def estimate_cost(legs: list[Any], rates: dict[str, Any]) -> CostRange:
    """按距离、模式费率、燃油和装卸基准估算费用区间。"""
    mode_costs: list[dict[str, float | str]] = []
    total = 0.0
    for leg in legs:
        rate = float(rates["mode_rates_per_km"].get(leg.mode, 1.0))
        base = leg.distance_km * rate
        fuel = base * float(rates["fuel_surcharge_ratio"].get(leg.mode, 0.1))
        handling_key = "airport" if leg.mode == "air" else "port" if leg.mode == "sea" else "rail_terminal"
        handling = float(rates["handling_fees"].get(handling_key, 0))
        leg_total = base + fuel + handling
        total += leg_total
        mode_costs.append({"mode": leg.mode, "distance_km": leg.distance_km, "rate": rate, "fuel": fuel, "handling": handling})
    tariff = total * float(rates.get("optional_tariff_rate", 0))
    total += tariff
    uncertainty = rates["uncertainty"]
    return CostRange(
        currency=rates.get("currency", "USD"), min=round(total * uncertainty["min_ratio"], 2),
        most_likely=round(total, 2), max=round(total * uncertainty["max_ratio"], 2),
        formula_explanation="各腿距离×模式费率+燃油附加费+装卸费+可选关税",
        input_snapshot={"legs": mode_costs, "tariff": tariff, "rates": rates},
    )


def calculate_risk(signals: dict[str, float], strategy: Any, evidence_refs: list[str] | None = None) -> RiskResult:
    """按配置权重聚合新闻、天气、拥堵、制裁和时刻可靠性风险。"""
    aliases = {
        "news_weight": "news", "weather_weight": "weather", "congestion_weight": "congestion",
        "sanctions_weight": "sanctions", "schedule_reliability_weight": "schedule_reliability",
    }
    weighted = 0.0
    used_weight = 0.0
    factors = []
    labels = {"news": "新闻事件", "weather": "天气海况", "congestion": "拥堵", "sanctions": "制裁禁运", "schedule_reliability": "时刻可靠性"}
    for weight_key, signal_key in aliases.items():
        value = float(signals.get(signal_key, 0))
        weight = float(strategy.risk_weights[weight_key])
        weighted += value * weight
        used_weight += weight
        factors.append(f"{labels[signal_key]}风险 {value:.0f}/100，权重 {weight:.0%}")
    score = round(weighted / used_weight if used_weight else 0, 2)
    level = "critical" if score >= strategy.critical_risk_threshold else "high" if score >= strategy.high_risk_threshold else "medium" if score >= 30 else "low"
    return RiskResult(risk_score=score, risk_level=level, risk_factors=factors, evidence_refs=evidence_refs or [])


def rank_routes(routes: list[RouteRecord], strategy_name: str, strategy: Any) -> list[RouteRecord]:
    """支持最低风险、最低成本、最快到达和混合评分。"""
    if not routes:
        return routes
    maximum_cost = max(route.estimated_cost.most_likely for route in routes if route.estimated_cost) or 1
    maximum_time = max(route.estimated_duration_h for route in routes) or 1
    for route in routes:
        inverse_risk = 1 - (route.risk.risk_score if route.risk else 50) / 100
        inverse_cost = 1 - (route.estimated_cost.most_likely if route.estimated_cost else maximum_cost) / maximum_cost
        inverse_duration = 1 - route.estimated_duration_h / maximum_time
        if strategy_name == "min_risk":
            route.score = inverse_risk
        elif strategy_name == "min_cost":
            route.score = inverse_cost
        elif strategy_name == "fastest":
            route.score = inverse_duration
        else:
            weights = strategy.ranking_weights
            route.score = round(weights["risk_weight"] * inverse_risk + weights["cost_weight"] * inverse_cost + weights["speed_weight"] * inverse_duration + weights["confidence_weight"] * route.confidence, 4)
        route.why_recommended = [f"综合排序得分 {route.score:.3f}", f"风险 {route.risk.risk_score if route.risk else 0:.1f}/100", f"预计费用 {route.estimated_cost.most_likely if route.estimated_cost else 0:.2f} {route.estimated_cost.currency if route.estimated_cost else 'USD'}"]
    return sorted(routes, key=lambda route: route.score, reverse=True)
