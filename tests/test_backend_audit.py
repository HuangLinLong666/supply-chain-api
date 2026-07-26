from scripts.audit_current_backend import component_sizes, neutral_default_factors, source_status


def test_neutral_default_factors_detects_only_neutral_placeholders():
    value = {
        "weather_risk": {"value": 0.5},
        "conflict_risk": {"value": 50},
        "observed_delay": {"value": 0.72},
        "missing": {"value": None},
    }
    assert neutral_default_factors(value) == ["conflict_risk", "weather_risk"]


def test_source_status_separates_synthetic_and_attributed_data():
    assert source_status("GDELT DOC 2.0 API") == "attributed"
    assert source_status("standard_skeleton_reference") == "synthetic_or_reference"
    assert source_status(None, "") == "unattributed"


def test_component_sizes_reports_disconnected_route_subgraphs():
    assert component_sizes([("A", "B"), ("B", "C"), ("X", "Y")]) == [3, 2]
