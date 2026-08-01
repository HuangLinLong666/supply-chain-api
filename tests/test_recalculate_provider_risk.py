from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

from app.provider_risk import PROVIDER_RISK_VERSION
from app.vehicle_network.core import load_strategy
from scripts.recalculate_provider_risk import (
    EXECUTE_CONFIRMATION,
    build_clear_rows,
    build_risk_factor_plan,
    build_segment_recalculation,
    parse_args,
)


NOW = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)


def factor(element_id: str, source: str = "derived_from_route_segment") -> dict:
    return {
        "element_id": element_id,
        "labels": ["RiskFactor"],
        "properties": {"source": source, "provider": None},
        "evidence_count": 0,
    }


def relationship(candidate_id: str, labels: list[str], properties: dict | None = None) -> dict:
    return {
        "element_id": "relationship-1",
        "type": "HAS_RISK",
        "properties": {},
        "start_element_id": "structural-node",
        "start_labels": labels,
        "start_properties": properties or {},
        "end_element_id": candidate_id,
        "end_labels": ["RiskFactor"],
        "end_properties": {"source": "derived_from_route_segment"},
    }


def segment(properties: dict, news_zones: list[dict] | None = None) -> dict:
    return {
        "element_id": "segment-1",
        "properties": {"segment_id": "SEG-1", "mode": "sea", **properties},
        "news_zones": news_zones or [],
        "weather_evidence": [],
    }


def test_unverified_factor_linked_to_structural_node_can_be_deleted():
    plan = build_risk_factor_plan([factor("factor-1")], [relationship("factor-1", ["RouteSegment"])])
    assert plan["deletion_ids"] == ["factor-1"]


def test_factor_linked_to_realtime_observation_is_blocked():
    plan = build_risk_factor_plan(
        [factor("factor-1")],
        [relationship("factor-1", ["NewsRiskEvent", "RiskObservation"], {"provider": "GDELT"})],
    )
    assert plan["deletion_ids"] == []
    assert plan["dispositions"]["factor-1"]["status"] == "blocked"


def test_structural_port_weather_properties_do_not_preserve_fake_factor():
    plan = build_risk_factor_plan(
        [factor("factor-1", "port_risk_standardization")],
        [relationship("factor-1", ["Port"], {"weather_source": "Open-Meteo Forecast API"})],
    )
    assert plan["deletion_ids"] == ["factor-1"]


def test_reviewed_or_provider_backed_factor_is_retained():
    reviewed = factor("reviewed")
    reviewed["properties"]["review_status"] = "approved"
    provider = factor("provider")
    provider["properties"]["provider"] = "Official Registry"
    plan = build_risk_factor_plan([reviewed, provider], [])
    assert plan["deletion_ids"] == []


def test_segment_recalculation_uses_active_gdelt_and_is_idempotent():
    row = segment(
        {
            "news_risk_score": 0.72,
            "news_risk_factors_json": '{"war":{"score":0.72,"confidence":0.8,"evidence":["cluster-war"],"observedAt":"2026-07-26T11:00:00+00:00"}}',
            "news_risk_updated_at": "2026-07-26T11:00:00+00:00",
            "news_risk_expires_at": "2026-07-26T18:00:00+00:00",
            "riskScore": 0.5,
        },
        [{"zone_id": "red-sea", "provider": "GDELT"}],
    )
    update = build_segment_recalculation(
        row,
        now=NOW,
        max_weather_age_hours=6,
        strategy=load_strategy(),
    )
    assert update is not None
    assert update["result"]["score_100"] == 72
    assert update["result"]["status"] == "partial"
    row["properties"].update(update["properties"])
    row["properties"].pop("riskScore")
    assert (
        build_segment_recalculation(
            row,
            now=NOW,
            max_weather_age_hours=6,
            strategy=load_strategy(),
        )
        is None
    )


def test_expired_news_produces_null_instead_of_neutral_default():
    update = build_segment_recalculation(
        segment(
            {
                "news_risk_score": 0.8,
                "news_risk_expires_at": "2026-07-26T10:00:00+00:00",
                "riskScore": 0.5,
            },
            [{"zone_id": "red-sea", "provider": "GDELT"}],
        ),
        now=NOW,
        max_weather_age_hours=6,
        strategy=load_strategy(),
    )
    assert update is not None
    assert update["properties"]["provider_risk_score"] is None
    assert update["properties"]["provider_risk_status"] == "unavailable"


def test_route_weather_snapshot_property_evidence_survives_recalculation():
    update = build_segment_recalculation(
        segment(
            {
                "route_weather_risk": 64,
                "route_weather_provider": "Open-Meteo",
                "route_weather_updated_at": "2026-07-26T11:30:00+00:00",
                "route_weather_expires_at": "2026-07-26T14:00:00+00:00",
                "route_weather_confidence": 0.45,
                "route_weather_evidence": ["route-weather-one"],
            }
        ),
        now=NOW,
        max_weather_age_hours=6,
        strategy=load_strategy(),
    )
    assert update is not None
    assert update["result"]["score_100"] == 64
    assert update["signals"]["natural_disaster"]["evidence"] == ["route-weather-one"]


def test_sanitized_clear_row_is_not_selected_again():
    node = {
        "element_id": "supplier-1",
        "labels": ["Supplier"],
        "identity": {"id": "SUP-1"},
        "properties": {"supplier_risk": 0.5},
    }
    rows = build_clear_rows([node], NOW)
    assert len(rows) == 1
    node["properties"] = rows[0]["properties"]
    assert node["properties"]["risk_scoring_version"] == PROVIDER_RISK_VERSION
    assert build_clear_rows([node], NOW) == []


def test_execute_requires_exact_confirmation(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["recalculate_provider_risk.py", "--execute"])
    with pytest.raises(SystemExit) as error:
        parse_args()
    assert error.value.code == 2
    monkeypatch.setattr(
        sys,
        "argv",
        ["recalculate_provider_risk.py", "--execute", "--confirm", EXECUTE_CONFIRMATION],
    )
    assert parse_args().execute is True


def test_delete_query_is_scoped_to_riskfactor_element_ids():
    source = Path("scripts/recalculate_provider_risk.py").read_text(encoding="utf-8")
    assert "MATCH (factor:RiskFactor) WHERE elementId(factor)=element_id" in source
    assert "MATCH (n) DETACH DELETE n" not in source
