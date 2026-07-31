import app.main as main
from app.route_optimizer import add_coordinate_fallbacks
from gdelt.exposure import exposed_zone_ids


def test_openapi_exposes_stage6_geospatial_endpoints():
    paths = main.app.openapi()["paths"]
    assert "/api/geography/locations" in paths
    assert "/api/geography/zones" in paths
    assert "/api/geography/segments/{segment_id}" in paths


def test_route_graph_uses_spatial_exposure_and_rejects_cross_ocean(monkeypatch):
    queries = []
    monkeypatch.setattr(main, "_route_graph_cache", None)
    monkeypatch.setattr(
        main,
        "safe_query",
        lambda query, parameters=None: queries.append(query) or [],
    )

    assert main.route_graph_segments() == []
    assert "PASSES_THROUGH" in queries[0]
    assert "invalid_cross_ocean" in queries[0]
    assert "geometry_geojson" in queries[0]
    assert "fromNode:TransportLocation" in queries[0]
    assert "toNode:TransportLocation" in queries[0]


def test_coordinate_fallback_never_uses_graph_neighbor_estimate():
    segments = [
        {
            "from_id": "A",
            "from_city": "Shanghai",
            "from_lat": 31.23,
            "from_lng": 121.47,
            "from_coordinate_source": "UN/LOCODE",
            "from_coordinate_status": "reference",
            "from_coordinate_confidence": 0.85,
            "to_id": "B",
            "to_city": "Unknown",
            "to_lat": None,
            "to_lng": None,
        }
    ]

    add_coordinate_fallbacks(segments)

    assert segments[0]["to_lat"] is None
    assert "to_coordinate_source" not in segments[0]


def test_geography_segment_decodes_geojson(monkeypatch):
    monkeypatch.setattr(
        main,
        "safe_query",
        lambda query, parameters=None: [
            {
                "segmentId": "SEG-1",
                "geometry": '{"type":"LineString","coordinates":[[1,2],[3,4]]}',
                "exposures": [],
            }
        ],
    )

    result = main.geography_segment("SEG-1")

    assert result["geometry"]["type"] == "LineString"


def test_cities_returns_location_id_as_frontend_identifier(monkeypatch):
    captured = []
    monkeypatch.setattr(
        main,
        "safe_query",
        lambda query, parameters=None: captured.append(query) or [],
    )

    assert main.cities() == {"count": 0, "cities": []}
    assert "AS id" in captured[0]
    assert "location_aliases" in captured[0]


def test_gdelt_prefers_active_passes_through_relationships():
    segment = {
        "geospatial_version": "geospatial-routing-v1",
        "spatial_exposures": [
            {"zone_id": "pacific-ocean", "active": True},
            {"zone_id": "red-sea", "active": False},
        ],
        "mode": "sea",
        "from_country": "China",
        "to_country": "Germany",
    }

    result = exposed_zone_ids(
        segment,
        [{"id": "red-sea", "aliases": []}, {"id": "pacific-ocean", "aliases": []}],
    )

    assert result == ["pacific-ocean"]
