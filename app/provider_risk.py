from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


PROVIDER_RISK_VERSION = "provider-risk-v1"

FACTOR_LABELS = {
    "weather": "天气与自然条件",
    "piracy": "海盗与海上安全",
    "port_congestion": "港口拥堵",
    "geopolitical": "地缘政治与冲突",
    "sanctions": "制裁与禁运",
    "schedule_reliability": "班期可靠性",
    "border_customs": "边境与海关",
    "infrastructure": "铁路基础设施",
    "traffic": "道路拥堵",
    "road_security": "陆路治安",
    "regulatory": "道路监管",
    "airspace_conflict": "空域冲突与关闭",
    "airport_capacity": "机场货运容量",
    "cargo_handling": "航空货物装卸",
}

NEWS_FACTOR_BY_MODE = {
    "sea": "geopolitical",
    "rail": "geopolitical",
    "air": "airspace_conflict",
}


def normalize_score_100(value: Any) -> float | None:
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if 0 <= score <= 1:
        score *= 100
    if not 0 <= score <= 100:
        return None
    return round(score, 4)


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_fresh(value: Any, *, now: datetime, max_age_hours: float) -> bool:
    observed_at = parse_datetime(value)
    if observed_at is None:
        return False
    age_seconds = (now.astimezone(timezone.utc) - observed_at).total_seconds()
    return 0 <= age_seconds <= max_age_hours * 3600


def build_segment_signals(
    mode: str | None,
    *,
    news_score: Any = None,
    news_provider: str | None = None,
    news_observed_at: Any = None,
    news_expires_at: Any = None,
    news_confidence: Any = None,
    news_evidence: list[str] | None = None,
    weather_score: Any = None,
    weather_provider: str | None = None,
    weather_observed_at: Any = None,
    weather_expires_at: Any = None,
    weather_confidence: Any = None,
    weather_evidence: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    canonical_mode = str(mode or "").casefold()
    signals: dict[str, dict[str, Any]] = {}
    news_factor = NEWS_FACTOR_BY_MODE.get(canonical_mode)
    normalized_news = normalize_score_100(news_score)
    if news_factor and normalized_news is not None and news_provider:
        signals[news_factor] = {
            "score": normalized_news,
            "provider": news_provider,
            "observed_at": str(news_observed_at) if news_observed_at is not None else None,
            "expires_at": str(news_expires_at) if news_expires_at is not None else None,
            "confidence": normalize_score_100(news_confidence),
            "evidence": list(news_evidence or []),
            "status": "available",
        }
    normalized_weather = normalize_score_100(weather_score)
    if canonical_mode in {"sea", "rail", "road", "air"} and normalized_weather is not None and weather_provider:
        signals["weather"] = {
            "score": normalized_weather,
            "provider": weather_provider,
            "observed_at": str(weather_observed_at) if weather_observed_at is not None else None,
            "expires_at": str(weather_expires_at) if weather_expires_at is not None else None,
            "confidence": normalize_score_100(weather_confidence),
            "evidence": list(weather_evidence or []),
            "status": "available",
        }
    return signals


def calculate_provider_risk(mode: str | None, signals: dict[str, dict[str, Any]], strategy: Any) -> dict[str, Any]:
    canonical_mode = str(mode or "").casefold()
    weights = strategy.mode_risk_weights.get(canonical_mode) or {}
    total_weight = sum(float(weight) for weight in weights.values())
    weighted_score = 0.0
    used_weight = 0.0
    confidence_weight = 0.0
    weighted_confidence = 0.0
    providers: set[str] = set()
    evidence: set[str] = set()
    factors: list[dict[str, Any]] = []
    observed_values: list[str] = []
    expires_values: list[str] = []

    for key, raw_weight in weights.items():
        weight = float(raw_weight)
        signal = signals.get(key) or {}
        provider = str(signal.get("provider") or "").strip()
        score = normalize_score_100(signal.get("score"))
        available = signal.get("status") == "available" and bool(provider) and score is not None
        factor = {
            "key": key,
            "label": FACTOR_LABELS.get(key, key),
            "weight": weight,
            "score": score if available else None,
            "status": "available" if available else "unavailable",
            "provider": provider or None,
            "observedAt": signal.get("observed_at") if available else None,
            "expiresAt": signal.get("expires_at") if available else None,
            "confidence": None,
            "evidence": list(signal.get("evidence") or []) if available else [],
        }
        if available:
            weighted_score += score * weight
            used_weight += weight
            providers.add(provider)
            evidence.update(str(item) for item in factor["evidence"] if item)
            if factor["observedAt"]:
                observed_values.append(str(factor["observedAt"]))
            if factor["expiresAt"]:
                expires_values.append(str(factor["expiresAt"]))
            confidence = normalize_score_100(signal.get("confidence"))
            if confidence is not None:
                factor["confidence"] = round(confidence / 100, 4)
                weighted_confidence += confidence * weight
                confidence_weight += weight
        factors.append(factor)

    completeness = round(used_weight / total_weight, 4) if total_weight else 0.0
    score_100 = round(weighted_score / used_weight, 2) if used_weight else None
    if score_100 is None:
        level = "unknown"
        status = "unavailable"
    else:
        level = (
            "critical"
            if score_100 >= strategy.critical_risk_threshold
            else "high"
            if score_100 >= strategy.high_risk_threshold
            else "medium"
            if score_100 >= 30
            else "low"
        )
        status = "available" if completeness >= 0.9999 else "partial"
    confidence = round((weighted_confidence / confidence_weight) / 100, 4) if confidence_weight else None
    missing_factors = [factor["key"] for factor in factors if factor["status"] == "unavailable"]
    payload = {
        "version": PROVIDER_RISK_VERSION,
        "mode": canonical_mode,
        "weights": weights,
        "factors": factors,
    }
    input_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "score": round(score_100 / 100, 6) if score_100 is not None else None,
        "score_100": score_100,
        "level": level,
        "status": status,
        "data_completeness": completeness,
        "confidence": confidence,
        "missing_factors": missing_factors,
        "providers": sorted(providers),
        "evidence": sorted(evidence),
        "factors": factors,
        "observed_at": max(observed_values) if observed_values else None,
        "expires_at": min(expires_values) if expires_values else None,
        "input_hash": input_hash,
        "scoring_version": PROVIDER_RISK_VERSION,
    }


def database_risk_properties(result: dict[str, Any], recalculated_at: datetime) -> dict[str, Any]:
    breakdown = {
        factor["key"]: {
            "value": round(factor["score"] / 100, 6) if factor["score"] is not None else None,
            "score100": factor["score"],
            "status": factor["status"],
            "provider": factor["provider"],
            "weight": factor["weight"],
            "observedAt": factor["observedAt"],
            "expiresAt": factor["expiresAt"],
            "confidence": factor["confidence"],
            "evidence": factor["evidence"],
        }
        for factor in result["factors"]
    }
    if result["score"] is None:
        explanation = "没有处于有效期内且带真实 Provider 的适用风险信号"
    else:
        available_labels = [factor["label"] for factor in result["factors"] if factor["status"] == "available"]
        explanation = f"仅使用真实 Provider 的可用维度重算：{', '.join(available_labels)}"
    return {
        "provider_risk_score": result["score"],
        "provider_risk_score_100": result["score_100"],
        "provider_risk_level": result["level"],
        "provider_risk_status": result["status"],
        "provider_risk_data_completeness": result["data_completeness"],
        "provider_risk_confidence": result["confidence"],
        "provider_risk_missing_factors": result["missing_factors"],
        "provider_risk_providers": result["providers"],
        "provider_risk_evidence": result["evidence"],
        "provider_risk_observed_at": result["observed_at"],
        "provider_risk_expires_at": result["expires_at"],
        "provider_risk_factors_json": json.dumps(result["factors"], ensure_ascii=False, separators=(",", ":")),
        "risk_breakdown": json.dumps(breakdown, ensure_ascii=False, separators=(",", ":")),
        "risk_explanation": explanation,
        "risk_status": result["status"],
        "risk_data_completeness": result["data_completeness"],
        "risk_scoring_version": result["scoring_version"],
        "risk_input_hash": result["input_hash"],
        "risk_recalculated_at": recalculated_at.isoformat(),
        "total_risk_score": result["score"],
        "dynamic_risk_score": result["score"],
    }
