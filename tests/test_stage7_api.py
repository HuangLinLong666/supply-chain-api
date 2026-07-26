import app.main as main


def test_openapi_exposes_stage7_ais_endpoints():
    paths = main.app.openapi()["paths"]
    assert "/health/ais" in paths
    assert "/api/providers/status" in paths
    assert "/api/ais/targets" in paths
    assert "/api/ais/targets/{target_id}/traffic" in paths
    assert "/api/ports/{port_id}/traffic" in paths
    assert "/api/vessels/{mmsi}" in paths


def test_ais_api_key_is_not_part_of_openapi_schema():
    schema_text = str(main.app.openapi()).casefold()
    assert "aisstream_api_key" not in schema_text
