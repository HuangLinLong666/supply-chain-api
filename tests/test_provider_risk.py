from datetime import datetime, timezone

from app.provider_risk import (
    build_segment_signals,
    calculate_provider_risk,
    database_risk_properties,
    is_fresh,
    normalize_score_100,
)
from app.vehicle_network.core import load_strategy


def test_score_normalization_supports_zero_to_one_and_zero_to_hundred():
    assert normalize_score_100(0.42) == 42
    assert normalize_score_100(42) == 42
    assert normalize_score_100(101) is None


def test_no_provider_signal_produces_unavailable_not_neutral_default():
    result = calculate_provider_risk("sea", {}, load_strategy())
    assert result["score"] is None
    assert result["score_100"] is None
    assert result["status"] == "unavailable"
    assert result["data_completeness"] == 0


def test_risk_uses_only_available_war_and_natural_disaster_signals():
    signals = build_segment_signals(
        "sea",
        news_score=0.6,
        news_provider="GDELT",
        weather_score=40,
        weather_provider="Open-Meteo",
    )
    result = calculate_provider_risk("sea", signals, load_strategy())
    expected = (60 * 0.40 + 40 * 0.35) / (0.40 + 0.35)
    assert result["score_100"] == round(expected, 2)
    assert result["data_completeness"] == 0.75
    assert result["status"] == "partial"
    assert set(result["providers"]) == {"GDELT", "Open-Meteo"}


def test_signal_without_provider_is_ignored():
    signals = {"natural_disaster": {"score": 80, "status": "available", "provider": None}}
    result = calculate_provider_risk("road", signals, load_strategy())
    assert result["score"] is None
    assert "natural_disaster" in result["missing_factors"]


def test_only_three_supported_factors_are_returned():
    signals = build_segment_signals("sea")
    result = calculate_provider_risk("sea", signals, load_strategy())
    assert [factor["key"] for factor in result["factors"]] == ["war", "natural_disaster", "trade_policy"]


def test_ais_congestion_is_observable_but_not_part_of_three_factor_score():
    sea_signals = build_segment_signals(
        "sea",
        congestion_score=72,
        congestion_provider="AISStream.io",
        congestion_observed_at="2026-07-26T12:00:00+00:00",
        congestion_expires_at="2026-07-26T13:30:00+00:00",
        congestion_confidence=0.8,
        congestion_evidence=["aisstream:port-shanghai:20260726T1200Z"],
    )
    sea_result = calculate_provider_risk("sea", sea_signals, load_strategy())
    assert sea_result["score_100"] is None
    assert sea_result["providers"] == []

    rail_signals = build_segment_signals(
        "rail",
        congestion_score=72,
        congestion_provider="AISStream.io",
    )
    assert "port_congestion" not in rail_signals


def test_war_news_is_supported_for_road_rerouting():
    signals = build_segment_signals("road", news_score=90, news_provider="GDELT")
    assert set(signals) == {"war"}
    assert calculate_provider_risk("road", signals, load_strategy())["score_100"] == 90


def test_database_properties_keep_missing_values_null():
    result = calculate_provider_risk("air", {}, load_strategy())
    properties = database_risk_properties(result, datetime(2026, 7, 26, tzinfo=timezone.utc))
    assert properties["provider_risk_score"] is None
    assert properties["total_risk_score"] is None
    assert properties["provider_risk_status"] == "unavailable"
    assert "0.5" not in properties["risk_explanation"]


def test_freshness_rejects_future_and_stale_observations():
    now = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    assert is_fresh("2026-07-26T10:00:00+00:00", now=now, max_age_hours=3)
    assert not is_fresh("2026-07-26T08:00:00+00:00", now=now, max_age_hours=3)
    assert not is_fresh("2026-07-26T13:00:00+00:00", now=now, max_age_hours=3)
