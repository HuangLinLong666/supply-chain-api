from shapely.geometry import box, mapping

from scripts.migrate_geospatial_data import (
    build_exposure_rows,
    build_location_rows,
    build_segment_geometry,
    build_zone_rows,
    simplify_network_coordinates,
)


GENERATED_AT = "2026-07-26T00:00:00+00:00"


def location(location_id, latitude, longitude):
    return {
        "location_id": location_id,
        "latitude": latitude,
        "longitude": longitude,
        "coordinate_status": "reference",
        "coordinate_confidence": 0.9,
    }


def segment(**overrides):
    row = {
        "element_id": "4:segment:1",
        "segment_id": "SEG-1",
        "mode": "road",
        "data_status": "estimated",
        "from_id": "ORIGIN",
        "to_id": "DESTINATION",
        "from_country": "China",
        "to_country": "United States",
    }
    row.update(overrides)
    return row


def test_cross_ocean_road_is_invalidated_before_routing():
    locations = {
        "ORIGIN": location("ORIGIN", 31.23, 121.47),
        "DESTINATION": location("DESTINATION", 33.74, -118.28),
    }
    land_mask = {"geometry_geojson": mapping(box(110.0, 20.0, 130.0, 45.0))}

    result = build_segment_geometry(
        segment(),
        locations,
        land_mask,
        GENERATED_AT,
        enable_osrm=False,
    )

    assert result["geometry_geojson"] is None
    assert result["geometry_status"] == "invalid_cross_ocean"
    assert result["feasibility_status"] == "invalid_cross_ocean"


def test_same_country_short_coastal_route_remains_unverified_fallback():
    locations = {
        "ORIGIN": location("ORIGIN", 29.87, 121.54),
        "DESTINATION": location("DESTINATION", 31.23, 121.47),
    }
    land_mask = {"geometry_geojson": mapping(box(0.0, 0.0, 1.0, 1.0))}

    result = build_segment_geometry(
        segment(from_country="China", to_country="China"),
        locations,
        land_mask,
        GENERATED_AT,
        enable_osrm=False,
    )

    assert result["geometry_status"] == "estimated_endpoint_fallback"
    assert result["feasibility_status"] == "unverified"


def test_synthetic_segment_never_receives_geometry():
    result = build_segment_geometry(
        segment(data_status="synthetic"),
        {},
        {"geometry_geojson": mapping(box(0, 0, 1, 1))},
        GENERATED_AT,
    )

    assert result["geometry_geojson"] is None
    assert result["geometry_status"] == "unavailable_synthetic"


def test_network_geometry_is_simplified_for_frontend_payloads():
    coordinates = [[10 + index / 1000, 20 + (index % 2) / 10000] for index in range(1000)]

    simplified = simplify_network_coordinates(coordinates)

    assert simplified[0] == coordinates[0]
    assert simplified[-1] == coordinates[-1]
    assert len(simplified) < len(coordinates) / 10


def test_hash_comparison_makes_location_and_zone_writes_idempotent():
    location_reference = {
        "L1": {
            "location_id": "L1",
            "latitude": 1.0,
            "longitude": 2.0,
            "coordinate_source": "provider",
            "coordinate_source_url": "https://example.test",
            "coordinate_license": "public-domain",
            "coordinate_collected_at": GENERATED_AT,
            "coordinate_confidence": 0.9,
            "coordinate_status": "reference",
            "coordinate_record_id": "L1",
            "canonical_unlocode": None,
            "identity_status": None,
            "coordinate_hash": "location-hash",
        }
    }
    existing_locations = [
        {
            "element_id": "4:location:1",
            "location_id": "L1",
            "coordinate_hash": "location-hash",
        }
    ]
    _, location_writes = build_location_rows(
        existing_locations,
        location_reference,
        GENERATED_AT,
    )
    zone_reference = {
        "Z1": {
            "zone_id": "Z1",
            "name": "zone",
            "zone_type": "test",
            "geometry_geojson": mapping(box(0, 0, 1, 1)),
            "geometry_source": "provider",
            "geometry_source_url": "https://example.test",
            "geometry_license": "public-domain",
            "geometry_collected_at": GENERATED_AT,
            "geometry_status": "reference",
            "geometry_confidence": 0.9,
            "applicable_modes": ["sea"],
            "geometry_is_navigational": False,
            "geometry_hash": "zone-hash",
        }
    }
    _, zone_writes = build_zone_rows(
        [{"zone_id": "Z1", "geometry_hash": "zone-hash"}],
        zone_reference,
        GENERATED_AT,
    )

    assert location_writes == []
    assert zone_writes == []


def test_existing_active_exposure_with_same_hash_is_not_rewritten():
    segment_row = segment(mode="sea", from_country="China", to_country="Germany")
    geometry_row = {
        "element_id": segment_row["element_id"],
        "segment_id": segment_row["segment_id"],
        "geometry_geojson": None,
        "geometry_status": "estimated_endpoint_fallback",
        "geometry_confidence": 0.0,
        "geometry_hash": "geometry-hash",
    }
    zone = {
        "zone_id": "red-sea",
        "geometry_geojson": '{"type":"Polygon","coordinates":[]}',
        "geometry_confidence": 0.7,
        "geometry_hash": "zone-hash",
        "applicable_modes": ["sea"],
    }
    risk_zones = [
        {
            "id": "red-sea",
            "aliases": [],
        }
    ]
    desired, writes, _ = build_exposure_rows(
        [segment_row],
        [geometry_row],
        [zone],
        risk_zones,
        [],
    )
    assert len(desired) == 1
    assert len(writes) == 1

    _, repeated_writes, deactivations = build_exposure_rows(
        [segment_row],
        [geometry_row],
        [zone],
        risk_zones,
        [
            {
                "element_id": segment_row["element_id"],
                "segment_id": segment_row["segment_id"],
                "zone_id": "red-sea",
                "active": True,
                "exposure_hash": desired[0]["exposure_hash"],
            }
        ],
    )

    assert repeated_writes == []
    assert deactivations == []


def test_invalid_cross_ocean_segment_has_no_active_exposure():
    segment_row = segment(mode="road", from_country="China", to_country="United States")
    geometry_row = {
        "element_id": segment_row["element_id"],
        "segment_id": segment_row["segment_id"],
        "geometry_geojson": None,
        "geometry_status": "invalid_cross_ocean",
        "geometry_confidence": 0.0,
        "geometry_hash": "invalid-hash",
    }

    desired, writes, deactivations = build_exposure_rows(
        [segment_row],
        [geometry_row],
        [
            {
                "zone_id": "port-shanghai",
                "geometry_geojson": mapping(box(120, 30, 122, 32)),
                "geometry_confidence": 0.5,
                "geometry_hash": "zone-hash",
                "applicable_modes": ["road"],
            }
        ],
        [{"id": "port-shanghai", "aliases": ["shanghai"]}],
        [
            {
                "element_id": segment_row["element_id"],
                "segment_id": segment_row["segment_id"],
                "zone_id": "port-shanghai",
                "active": True,
                "exposure_hash": "old-hash",
            }
        ],
    )

    assert desired == []
    assert writes == []
    assert deactivations == [
        {
            "element_id": segment_row["element_id"],
            "segment_id": segment_row["segment_id"],
            "zone_id": "port-shanghai",
        }
    ]
