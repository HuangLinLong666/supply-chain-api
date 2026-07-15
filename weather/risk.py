"""Transparent deterministic weather-risk scoring."""

from __future__ import annotations

from statistics import mean
from typing import Any

from weather.config import load_rules


def interpolate(value: float, points: list[tuple[float, float]]) -> float:
    if value <= points[0][0]: return points[0][1]
    for (left_x, left_y), (right_x, right_y) in zip(points, points[1:]):
        if value <= right_x:
            return left_y + (value - left_x) * (right_y - left_y) / (right_x - left_x)
    return points[-1][1]


def wind_risk(value: float) -> float: return interpolate(value, [(0, 0), (20, 15), (40, 40), (60, 70), (80, 90), (100, 100)])
def gust_risk(value: float) -> float: return interpolate(value, [(0, 0), (30, 15), (50, 40), (70, 70), (90, 90), (110, 100)])
def precipitation_risk(value: float) -> float: return interpolate(value, [(0, 0), (1, 10), (5, 35), (15, 70), (30, 100)])
def visibility_risk(value: float) -> float: return interpolate(value, [(0, 100), (1000, 80), (2000, 60), (5000, 30), (10000, 10), (20000, 0)])
def wave_risk(value: float) -> float: return interpolate(value, [(0, 0), (1, 15), (2, 35), (3, 60), (4, 80), (6, 100)])
def temperature_risk(value: float) -> float:
    if -10 <= value <= 40: return 5
    return min(100.0, 5 + (abs(value - (-10 if value < -10 else 40)) * 5))


def risk_level(score: float) -> str:
    if score < 25: return "LOW"
    if score < 50: return "MEDIUM"
    if score < 75: return "HIGH"
    return "CRITICAL"


def score_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    rules = load_rules()
    values = {
        "wind_risk": None if metrics.get("wind_speed_10m") is None else wind_risk(float(metrics["wind_speed_10m"])),
        "gust_risk": None if metrics.get("wind_gusts_10m") is None else gust_risk(float(metrics["wind_gusts_10m"])),
        "precipitation_risk": None if metrics.get("precipitation") is None else precipitation_risk(float(metrics["precipitation"])),
        "visibility_risk": None if metrics.get("visibility") is None else visibility_risk(float(metrics["visibility"])),
        "wave_risk": None if metrics.get("wave_height") is None else wave_risk(float(metrics["wave_height"])),
        "temperature_risk": None if metrics.get("temperature_2m") is None else temperature_risk(float(metrics["temperature_2m"])),
        "weather_code_risk": None if metrics.get("weather_code") is None else float(rules["weather_code_risk"].get(str(int(metrics["weather_code"])), 25)),
    }
    valid = {key: value for key, value in values.items() if value is not None}
    completeness = len(valid) / len(values)
    valid_weight = sum(rules["weights"][key] for key in valid)
    score = sum(value * rules["weights"][key] / valid_weight for key, value in valid.items()) if valid_weight else 0.0
    confidence = completeness * (0.85 if values["wave_risk"] is None else 1.0)
    factors = sorted(({"factor": key.removesuffix("_risk"), "risk_score": round(value, 1)} for key, value in valid.items()), key=lambda item: item["risk_score"], reverse=True)
    return {"score": round(score, 1), "level": risk_level(score) if completeness >= 0.4 else "INSUFFICIENT_DATA", "confidence": round(confidence, 3), "data_completeness": round(completeness, 3), "components": values, "factors": factors}


def calculate_risk(current: dict[str, Any], hourly: list[dict[str, Any]]) -> dict[str, Any]:
    current_result = score_metrics(current)
    forecasts = [score_metrics(item)["score"] for item in hourly[:24]]
    max6 = max(forecasts[:6], default=current_result["score"])
    max24 = max(forecasts, default=current_result["score"])
    avg24 = mean(forecasts) if forecasts else current_result["score"]
    deterioration = max24 - current_result["score"]
    trend = "RAPIDLY_WORSENING" if deterioration >= 25 else "WORSENING" if deterioration >= 10 else "IMPROVING" if deterioration <= -10 else "STABLE"
    top = current_result["factors"][:2]
    names = {"wave": "浪高", "gust": "强阵风", "wind": "持续风", "precipitation": "降水", "visibility": "低能见度", "weather_code": "天气现象", "temperature": "极端温度"}
    causes = "、".join(names.get(item["factor"], item["factor"]) for item in top) or "有效数据不足"
    summary = f"当前港口天气风险为{current_result['level']}，主要因素是{causes}。未来24小时趋势为{trend}。"
    return {**current_result, "max_risk_6h": round(max6, 1), "max_risk_24h": round(max24, 1), "average_risk_24h": round(avg24, 1), "deterioration_score": round(deterioration, 1), "trend": trend, "summary": summary}
