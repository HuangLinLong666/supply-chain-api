import pytest

from database.country_identity import (
    COUNTRY_NAMING_VERSION,
    canonical_country_fields,
    canonical_country_names,
    resolve_country_code,
)
from database.location_identity import canonical_location_id


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("US", "US"),
        ("United States", "US"),
        ("United States of America", "US"),
        ("USA", "US"),
        ("America", "US"),
        ("美国", "US"),
        ("UK", "GB"),
        ("Great Britain", "GB"),
        ("中国", "CN"),
        ("Côte d’Ivoire", "CI"),
    ],
)
def test_country_aliases_resolve_to_iso_alpha2(value, expected):
    assert resolve_country_code(value) == expected


def test_country_fields_use_cldr_display_names_and_keep_aliases():
    fields = canonical_country_fields({"country": "America"})
    assert fields is not None
    assert fields["country_code"] == "US"
    assert fields["country"] == "United States"
    assert fields["country_name_zh"] == "美国"
    assert fields["country_naming_version"] == COUNTRY_NAMING_VERSION
    assert "America" in fields["country_aliases"]


def test_invalid_or_ambiguous_region_is_not_guessed():
    assert resolve_country_code("North America") is None
    assert resolve_country_code("unknown") is None
    with pytest.raises(ValueError):
        canonical_country_names("XX")


def test_location_id_uses_iso_country_code_for_country_alias():
    location_id = canonical_location_id(
        ["Warehouse"],
        {"location_id": "west-coast-warehouse", "country": "America"},
        element_id="warehouse-us-1",
    )
    assert location_id == "WH-US-WEST-COAST"
