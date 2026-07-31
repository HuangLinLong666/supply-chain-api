import sys

import pytest

from database.location_identity import (
    LOCATION_ID_CONFIRMATION,
    build_location_id_plan,
    canonical_location_id,
)
from scripts.migrate_location_ids_v2 import parse_args


def node(element_id: str, labels: list[str], **properties):
    return {"element_id": element_id, "labels": labels, "properties": properties}


@pytest.mark.parametrize(
    ("labels", "properties", "expected"),
    [
        (["Port"], {"location_id": "CN-SHA", "canonical_unlocode": "CNSHG"}, "PORT-CNSHG"),
        (["Airport"], {"location_id": "CN-PVG", "iata": "PVG"}, "AIR-PVG"),
        (
            ["RailTerminal"],
            {"location_id": "ALASHANKOU_RAILWAY_PORT", "country": "China"},
            "RAIL-CN-ALASHANKOU",
        ),
        (
            ["Factory"],
            {"location_id": "FAC-CATL-ND", "factory_id": "FAC-CATL-ND", "country": "China"},
            "FAC-CN-CATL-ND",
        ),
        (
            ["Warehouse"],
            {"location_id": "DE-AWH", "warehouse_id": "DE-AWH", "country": "Germany"},
            "WH-DE-AWH",
        ),
    ],
)
def test_canonical_location_id_rules(labels, properties, expected):
    assert canonical_location_id(labels, properties, element_id="element-1") == expected


def test_plan_preserves_old_ids_as_aliases_and_is_unique():
    plan = build_location_id_plan(
        [
            node("port", ["Port"], location_id="CN-SHA", unlocode="CNSHA", canonical_unlocode="CNSHG"),
            node("airport", ["Airport"], location_id="CN-PVG", iata="PVG"),
        ]
    )
    assert plan["changed_count"] == 2
    assert plan["collision_count"] == 0
    assert {row["new_id"] for row in plan["rows"]} == {"PORT-CNSHG", "AIR-PVG"}
    assert "CN-SHA" in next(row for row in plan["rows"] if row["kind"] == "port")["aliases"]


def test_missing_country_is_inferred_from_known_chinese_city():
    result = canonical_location_id(
        ["Warehouse"],
        {
            "location_id": "loc:warehouse:test",
            "warehouse_id": "warehouse:shanghai-waigaoqiao-export-warehouse",
            "city": "Shanghai",
        },
        element_id="warehouse-1",
    )
    assert result == "WH-CN-SHANGHAI-WAIGAOQIAO-EXPORT"


def test_reference_aliases_prefer_the_node_that_owned_the_old_primary_id():
    plan = build_location_id_plan(
        [
            node("airport", ["Airport"], location_id="CAN", iata="CAN"),
            node("warehouse", ["Warehouse"], location_id="CAN~1234", warehouse_id="CAN", country="China"),
            node("port", ["Port"], location_id="US-LAX", unlocode="USLAX", canonical_unlocode="USLAX"),
        ]
    )
    airport = next(row for row in plan["rows"] if row["kind"] == "airport")
    warehouse = next(row for row in plan["rows"] if row["kind"] == "warehouse")
    port = next(row for row in plan["rows"] if row["kind"] == "port")
    assert "CAN" in airport["reference_aliases"]
    assert "CAN" not in warehouse["reference_aliases"]
    assert "USLAX" in port["reference_aliases"]


def test_execute_requires_exact_confirmation(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["migrate_location_ids_v2.py", "--execute"])
    with pytest.raises(SystemExit) as error:
        parse_args()
    assert error.value.code == 2

    monkeypatch.setattr(
        sys,
        "argv",
        ["migrate_location_ids_v2.py", "--execute", "--confirm", LOCATION_ID_CONFIRMATION],
    )
    assert parse_args().execute is True
