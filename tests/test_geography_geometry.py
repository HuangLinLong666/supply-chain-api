from shapely.geometry import LineString, box, mapping

from geography.geometry import (
    geometry_exposure,
    great_circle_coordinates,
    land_coverage_fraction,
)


def test_great_circle_preserves_endpoints_and_uses_short_dateline_arc():
    coordinates = great_circle_coordinates((35.0, 170.0), (35.0, -170.0), point_count=9)

    assert coordinates[0] == [170.0, 35.0]
    assert coordinates[-1] == [-170.0, 35.0]
    assert any(abs(point[0]) >= 179 for point in coordinates)


def test_geometry_exposure_handles_antimeridian():
    route = mapping(LineString([(170.0, 10.0), (-170.0, 10.0)]))
    zone = mapping(box(175.0, 5.0, 185.0, 15.0))

    exposure = geometry_exposure(route, zone)

    assert exposure is not None
    assert exposure["intersection_distance_km"] > 1000
    assert 0.45 <= exposure["exposure_ratio"] <= 0.55


def test_land_coverage_fraction_counts_sampled_points():
    land = mapping(box(0.0, 0.0, 5.0, 5.0))
    coordinates = [[1.0, 1.0], [2.0, 2.0], [10.0, 10.0], [20.0, 20.0]]

    assert land_coverage_fraction(coordinates, land) == 0.5
