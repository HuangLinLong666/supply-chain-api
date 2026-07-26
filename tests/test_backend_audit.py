from scripts.audit_current_backend import collect_api_inventory, component_sizes, neutral_default_factors, source_status


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


def test_api_inventory_includes_mounted_vehicle_and_ais_routers():
    routes = {(method, row["path"]) for row in collect_api_inventory() for method in row["methods"]}
    assert ("POST", "/api/v1/routes/generate") in routes
    assert ("GET", "/health/ais") in routes
    assert ("GET", "/api/providers/status") in routes
