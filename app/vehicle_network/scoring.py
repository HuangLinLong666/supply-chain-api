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


def calculate_risk(signals: dict[str, float | None], strategy: Any, evidence_refs: list[str] | None = None) -> RiskResult:
    """按配置权重聚合新闻、天气、拥堵、制裁和时刻可靠性风险。"""
    aliases = {
        "news_weight": "news", "weather_weight": "weather", "congestion_weight": "congestion",
        "sanctions_weight": "sanctions", "schedule_reliability_weight": "schedule_reliability",
    }
    weighted = 0.0
    used_weight = 0.0
    factors = []
    missing = []
    labels = {"news": "新闻事件", "weather": "天气海况", "congestion": "拥堵", "sanctions": "制裁禁运", "schedule_reliability": "时刻可靠性"}
    for weight_key, signal_key in aliases.items():
        weight = float(strategy.risk_weights[weight_key])
        raw_value = signals.get(signal_key)
        if raw_value is None:
            missing.append(labels[signal_key])
            continue
        value = float(raw_value)
        weighted += value * weight
        used_weight += weight
        factors.append(f"{labels[signal_key]}风险 {value:.0f}/100，权重 {weight:.0%}")
    total_weight = sum(float(value) for value in strategy.risk_weights.values()) or 1.0
    completeness = round(used_weight / total_weight, 4)
    if not used_weight:
        return RiskResult(
            risk_score=None,
            risk_level="unknown",
            risk_factors=[],
            evidence_refs=evidence_refs or [],
            data_completeness=0.0,
            missing_factors=missing,
        )
    score = round(weighted / used_weight, 2)
    level = "critical" if score >= strategy.critical_risk_threshold else "high" if score >= strategy.high_risk_threshold else "medium" if score >= 30 else "low"
    return RiskResult(
        risk_score=score,
        risk_level=level,
        risk_factors=factors,
        evidence_refs=evidence_refs or [],
        data_completeness=completeness,
        missing_factors=missing,
    )


MODE_RISK_LABELS = {
    "weather": "天气与自然条件", "piracy": "海盗与海上安全", "port_congestion": "港口拥堵",
    "geopolitical": "地缘政治与冲突", "sanctions": "制裁与禁运", "schedule_reliability": "班期可靠性",
    "border_customs": "边境与海关", "infrastructure": "铁路基础设施", "traffic": "道路拥堵",
    "road_security": "陆路治安", "regulatory": "道路监管", "airspace_conflict": "空域冲突与关闭",
    "airport_capacity": "机场货运容量", "cargo_handling": "航空货物装卸",
}


def calculate_mode_risk(mode: str, signals: dict[str, float | None], strategy: Any, evidence_refs: list[str] | None = None) -> RiskResult:
    """使用运输方式专属风险维度，避免海运与铁路返回相同因子。"""
    weights = strategy.mode_risk_weights.get(mode)
    if not weights:
        return calculate_risk(signals, strategy, evidence_refs)
    weighted = 0.0
    used_weight = 0.0
    factors = []
    missing = []
    for key, weight in weights.items():
        raw_value = signals.get(key)
        if raw_value is None:
            missing.append(MODE_RISK_LABELS.get(key, key))
            continue
        value = float(raw_value)
        weighted += value * float(weight)
        used_weight += float(weight)
        factors.append(f"{MODE_RISK_LABELS.get(key, key)}风险 {value:.0f}/100，权重 {float(weight):.0%}")
    total_weight = sum(float(value) for value in weights.values()) or 1.0
    completeness = round(used_weight / total_weight, 4)
    if used_weight == 0:
        return RiskResult(
            risk_score=None, risk_level="unknown", risk_factors=[], evidence_refs=evidence_refs or [],
            data_completeness=0.0, missing_factors=missing,
        )
    score = round(weighted / used_weight, 2)
    level = "critical" if score >= strategy.critical_risk_threshold else "high" if score >= strategy.high_risk_threshold else "medium" if score >= 30 else "low"
    return RiskResult(
        risk_score=score, risk_level=level, risk_factors=factors, evidence_refs=evidence_refs or [],
        data_completeness=completeness, missing_factors=missing,
    )


def rank_routes(routes: list[RouteRecord], strategy_name: str, strategy: Any) -> list[RouteRecord]:
    """支持最低风险、最低成本、最快到达和混合评分。"""
    if not routes:
        return routes
    maximum_cost = max(route.estimated_cost.most_likely for route in routes if route.estimated_cost) or 1
    maximum_time = max(route.estimated_duration_h for route in routes) or 1
    for route in routes:
        measured_risk = route.risk.risk_score if route.risk and route.risk.risk_score is not None else None
        inverse_risk = 1 - measured_risk / 100 if measured_risk is not None else 0.0
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
        risk_text = f"风险 {route.risk.risk_score:.1f}/100" if route.risk and route.risk.risk_score is not None else "风险数据不足，应用缺失惩罚，不生成中性风险分"
        route.why_recommended = [f"综合排序得分 {route.score:.3f}", risk_text, f"预计费用 {route.estimated_cost.most_likely if route.estimated_cost else 0:.2f} {route.estimated_cost.currency if route.estimated_cost else 'USD'}"]
    return sorted(routes, key=lambda route: route.score, reverse=True)
