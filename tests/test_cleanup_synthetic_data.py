import sys

import pytest

from scripts.cleanup_synthetic_data import (
    REDACTED_VALUE,
    build_cleanup_plan,
    normalize_filters,
    parse_args,
    protection_reasons,
    redact_sensitive_properties,
)


def node(element_id, labels, source=None, review_status=None):
    return {
        "element_id": element_id,
        "labels": labels,
        "properties": {"source": source, "review_status": review_status},
    }


def relationship(element_id, start, end, relationship_type="LINKS"):
    return {
        "element_id": element_id,
        "type": relationship_type,
        "properties": {},
        "start_element_id": start["element_id"],
        "start_labels": start["labels"],
        "start_properties": start["properties"],
        "end_element_id": end["element_id"],
        "end_labels": end["labels"],
        "end_properties": end["properties"],
    }


def test_filter_normalization_supports_repeated_and_comma_values():
    assert normalize_filters(["Synthetic, SAMPLE", "synthetic", " mock "]) == ["synthetic", "sample", "mock"]


def test_realtime_and_reviewed_nodes_are_hard_protected():
    assert protection_reasons(node("news", ["NewsRiskEvent"], "synthetic"))
    assert protection_reasons(node("weather", ["Other"], "Open-Meteo Forecast API"))
    assert protection_reasons(node("reviewed", ["Route"], "synthetic", "approved"))


def test_candidate_linked_to_realtime_data_is_blocked_with_dependencies():
    segment = node("segment", ["RouteSegment"], "synthetic")
    risk = node("risk", ["RiskFactor"], "synthetic")
    zone = node("zone", ["NewsRiskZone"], "GDELT DOC 2.0 API")
    relationships = [
        relationship("r1", segment, risk, "HAS_RISK"),
        relationship("r2", segment, zone, "EXPOSED_TO_NEWS_RISK"),
    ]
    plan = build_cleanup_plan([segment, risk], relationships)
    assert plan.deletion_ids == []
    assert plan.dispositions["segment"]["status"] == "blocked"
    assert plan.dispositions["risk"]["status"] == "blocked"


def test_isolated_synthetic_component_can_be_deleted():
    segment = node("segment", ["RouteSegment"], "synthetic")
    risk = node("risk", ["RiskFactor"], "synthetic")
    plan = build_cleanup_plan([segment, risk], [relationship("r1", segment, risk, "HAS_RISK")])
    assert plan.deletion_ids == ["risk", "segment"]


def test_boundary_link_blocks_candidate_unless_explicitly_allowed():
    candidate = node("candidate", ["RouteSegment"], "synthetic")
    retained = node("retained", ["Supplier"], "wey-gu/supplychain-dataset-gen")
    relationships = [relationship("r1", retained, candidate, "USES")]
    blocked = build_cleanup_plan([candidate], relationships)
    allowed = build_cleanup_plan([candidate], relationships, allow_boundary_links=True)
    assert blocked.deletion_ids == []
    assert allowed.deletion_ids == ["candidate"]


def test_backup_redacts_sensitive_properties_recursively():
    properties = {
        "name": "Shanghai",
        "neo4j_uri": "neo4j+s://example.databases.neo4j.io",
        "providerApiKey": "secret-value",
        "nested": {"access_token": "token-value", "source_url": "https://example.com"},
    }
    redacted = redact_sensitive_properties(properties)
    assert redacted["name"] == "Shanghai"
    assert redacted["neo4j_uri"] == REDACTED_VALUE
    assert redacted["providerApiKey"] == REDACTED_VALUE
    assert redacted["nested"]["access_token"] == REDACTED_VALUE
    assert redacted["nested"]["source_url"] == "https://example.com"


def test_execute_requires_an_explicit_filter(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["cleanup_synthetic_data.py", "--execute", "--confirm", "DELETE_SYNTHETIC_ONLY"],
    )
    with pytest.raises(SystemExit) as error:
        parse_args()
    assert error.value.code == 2


def test_execute_accepts_an_explicit_source_filter(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cleanup_synthetic_data.py",
            "--execute",
            "--source",
            "synthetic",
            "--confirm",
            "DELETE_SYNTHETIC_ONLY",
        ],
    )
    assert parse_args().execute is True
