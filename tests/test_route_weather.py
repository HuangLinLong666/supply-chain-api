from datetime import datetime, timedelta, timezone

from app.provider_risk import build_segment_signals, calculate_provider_risk
from app.route_optimizer import shortest_path
from app.vehicle_network.core import load_strategy
from weather.risk import score_metrics_for_mode
from weather.route_sampling import (
    aggregate_route_weather,
    build_route_samples,
    score_sample,
)


NOW = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)


def payload(hours: int = 4, **values):
    times = [(NOW + timedelta(hours=index)).isoformat() for index in range(hours)]
    hourly = {"time": times}
    for key, value in values.items():
        hourly[key] = [value for _ in times]
    return {"hourly": hourly}


def complete_weather(**overrides):
    metrics = {
        "wind_speed_10m": 10,
        "wind_gusts_10m": 15,
        "precipitation": 0,
        "snowfall": 0,
        "visibility": 20000,
        "wave_height": 0.2,
        "temperature_2m": 20,
        "weather_code": 0,
    }
    metrics.update(overrides)
    return metrics


def segment(**overrides):
    row = {
        "element_id": "segment-element",
        "segment_id": "SEG-1",
        "mode": "sea",
        "from_lat": 31.23,
        "from_lng": 121.47,
        "to_lat": 1.35,
        "to_lng": 103.82,
    }
    row.update(overrides)
    return row


def test_geometry_uses_multiple_route_samples():
    plan = build_route_samples(
        segment(
            geometry={
                "type": "LineString",
                "coordinates": [[121.47, 31.23], [115.0, 15.0], [103.82, 1.35]],
            }
        ),
        sample_count=5,
    )
    assert plan["sampling_method"] == "geometry_linestring"
    assert plan["geometry_status"] == "available"
    assert len(plan["samples"]) == 5
    assert plan["samples"][0]["latitude"] == 31.23
    assert plan["samples"][-1]["longitude"] == 103.82


def test_missing_geometry_is_explicit_low_confidence_endpoint_fallback():
    plan = build_route_samples(segment(), sample_count=5)
    assert plan["sampling_method"] == "endpoint_fallback"
    assert plan["sampling_confidence"] == 0.35
    assert len(plan["samples"]) == 2


def test_estimated_geometry_never_claims_verified_route_confidence():
    plan = build_route_samples(
        segment(
            source_type="estimated_by_graph",
            is_inferred=True,
            geometry={
                "type": "LineString",
                "coordinates": [[121.47, 31.23], [103.82, 1.35]],
            },
        )
    )
    assert plan["sampling_method"] == "estimated_geometry_linestring"
    assert plan["geometry_status"] == "estimated"
    assert plan["sampling_confidence"] == 0.55


def test_forecast_is_not_carried_past_available_horizon():
    plan = build_route_samples(segment())
    weather = payload(3, **complete_weather())
    departure = score_sample(
        plan["samples"][0],
        mode="sea",
        duration_hours=10,
        reference_time=NOW,
        weather_payload=weather,
        marine_payload=weather,
    )
    arrival = score_sample(
        plan["samples"][1],
        mode="sea",
        duration_hours=10,
        reference_time=NOW,
        weather_payload=weather,
        marine_payload=weather,
    )
    assert departure["score"] is not None
    assert arrival["score"] is None
    assert arrival["status"] == "unavailable_outside_forecast_horizon"


def test_transport_modes_use_different_weather_dimensions():
    metrics = complete_weather(wave_height=6)
    sea = score_metrics_for_mode(metrics, "sea")
    road = score_metrics_for_mode(metrics, "road")
    assert sea["score"] > road["score"]
    assert any(item["factor"] == "wave" for item in sea["factors"])
    assert all(item["factor"] != "wave" for item in road["factors"])


def test_endpoint_fallback_stays_partial_even_with_complete_provider_metrics():
    samples = [
        {"score": 20, "confidence": 1, "data_completeness": 1, "factors": []},
        {"score": 80, "confidence": 1, "data_completeness": 1, "factors": []},
    ]
    result = aggregate_route_weather(
        samples,
        sampling_method="endpoint_fallback",
        sampling_confidence=0.35,
    )
    assert result["score"] == 68
    assert result["status"] == "partial"
    assert result["confidence"] == 0.35


def test_open_meteo_route_weather_changes_provider_risk_and_min_risk_path():
    strategy = load_strategy()
    low = calculate_provider_risk(
        "sea",
        build_segment_signals(
            "sea",
            weather_score=10,
            weather_provider="Open-Meteo",
            weather_observed_at=NOW.isoformat(),
            weather_expires_at=(NOW + timedelta(hours=2)).isoformat(),
            weather_confidence=0.8,
            weather_evidence=["weather-low"],
        ),
        strategy,
    )
    high = calculate_provider_risk(
        "sea",
        build_segment_signals(
            "sea",
            weather_score=90,
            weather_provider="Open-Meteo",
            weather_observed_at=NOW.isoformat(),
            weather_expires_at=(NOW + timedelta(hours=2)).isoformat(),
            weather_confidence=0.8,
            weather_evidence=["weather-high"],
        ),
        strategy,
    )
    segments = [
        {"segment_id": "direct", "from_id": "A", "to_id": "B", "risk_score": high["score"], "risk_data_completeness": high["data_completeness"], "risk_status": high["status"]},
        {"segment_id": "safe-1", "from_id": "A", "to_id": "C", "risk_score": low["score"], "risk_data_completeness": low["data_completeness"], "risk_status": low["status"]},
        {"segment_id": "safe-2", "from_id": "C", "to_id": "B", "risk_score": low["score"], "risk_data_completeness": low["data_completeness"], "risk_status": low["status"]},
    ]
    result = shortest_path(segments, "A", "B", "min_risk", 1.0)
    assert low["score"] == 0.1
    assert high["score"] == 0.9
    assert [item["segment_id"] for item in result["segments"]] == ["safe-1", "safe-2"]
