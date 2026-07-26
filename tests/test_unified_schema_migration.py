import sys

import pytest

from database.unified_schema import IDENTITY_CONSTRAINTS, TARGET_LABELS
from scripts.migrate_unified_schema import (
    ALIAS_OPERATIONS,
    MIGRATION_CONFIRMATION,
    NORMALIZE_QUERY,
    RELATIONSHIP_OPERATIONS,
    build_location_plan,
    parse_args,
)


def location(element_id, labels, **properties):
    return {"element_id": element_id, "labels": labels, "properties": properties}


def test_location_plan_preserves_existing_id_and_adds_super_label():
    plan = build_location_plan(
        [location("port-1", ["Port"], location_id="CN-SHA", unlocode="CNSHA")]
    )
    assert plan["rows"][0]["location_id"] == "CN-SHA"
    assert plan["rows"][0]["labels_to_add"] == ["TransportLocation"]
    assert plan["projected_unique_location_ids"] == 1


def test_location_plan_disambiguates_duplicate_external_codes():
    plan = build_location_plan(
        [
            location("airport-1", ["Airport"], iata_code="PVG", name="Airport A"),
            location("airport-2", ["Airport"], iata_code="PVG", name="Airport B"),
        ]
    )
    assigned = {row["location_id"] for row in plan["rows"]}
    assert len(assigned) == 2
    assert "PVG" in assigned
    assert any(value.startswith("PVG~") for value in assigned)
    assert len(plan["conflicts_resolved"]) == 1


def test_legacy_warehouse_gets_both_canonical_labels_and_ids():
    plan = build_location_plan(
        [location("warehouse-1", ["ExportWarehouse"], name="Shanghai Export Warehouse")]
    )
    row = plan["rows"][0]
    assert row["labels_to_add"] == ["TransportLocation", "Warehouse"]
    assert row["properties"]["warehouse_id"].startswith("warehouse:")
    assert row["location_kind"] == "warehouse"


def test_every_stage_three_entity_has_an_identity_constraint_or_location_supertype():
    constrained_labels = {rule.label for rule in IDENTITY_CONSTRAINTS}
    subtype_labels = {"Port", "Airport", "RailTerminal", "RoadTerminal"}
    assert set(TARGET_LABELS) - subtype_labels <= constrained_labels
    assert "TransportLocation" in constrained_labels


def test_data_migration_queries_never_delete_or_create_nodes():
    queries = [NORMALIZE_QUERY]
    queries.extend(operation.write_query for operation in (*ALIAS_OPERATIONS, *RELATIONSHIP_OPERATIONS))
    normalized = "\n".join(queries).upper()
    assert "DETACH DELETE" not in normalized
    assert " DELETE " not in normalized
    assert "CREATE (" not in normalized


def test_execute_requires_confirmation(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["migrate_unified_schema.py", "--execute"])
    with pytest.raises(SystemExit) as error:
        parse_args()
    assert error.value.code == 2


def test_execute_accepts_exact_confirmation(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["migrate_unified_schema.py", "--execute", "--confirm", MIGRATION_CONFIRMATION],
    )
    assert parse_args().execute is True
