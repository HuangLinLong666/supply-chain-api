from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from typing import Any

from shapely.geometry import LineString, Point, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform


GEOSPATIAL_VERSION = "geospatial-routing-v1"
EARTH_RADIUS_KM = 6371.0088


def valid_coordinate(latitude: Any, longitude: Any) -> bool:
    try:
        lat = float(latitude)
        lng = float(longitude)
    except (TypeError, ValueError):
        return False
    return -90 <= lat <= 90 and -180 <= lng <= 360 and not (lat == 0 and lng == 0)


def normalize_longitude(longitude: float) -> float:
    normalized = ((float(longitude) + 180.0) % 360.0) - 180.0
    return 180.0 if normalized == -180.0 and longitude > 0 else normalized


def parse_geojson(value: Any) -> BaseGeometry | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, dict):
        return None
    try:
        geometry = shape(value)
    except (TypeError, ValueError):
        return None
    if geometry.is_empty:
        return None
    if not geometry.is_valid and geometry.geom_type in {"Polygon", "MultiPolygon"}:
        geometry = geometry.buffer(0)
    return None if geometry.is_empty else geometry


def _rounded_coordinates(value: Any, precision: int) -> Any:
    if isinstance(value, (list, tuple)):
        return [_rounded_coordinates(item, precision) for item in value]
    if isinstance(value, float):
        return round(value, precision)
    return value


def geometry_mapping(geometry: BaseGeometry, precision: int = 6) -> dict[str, Any]:
    payload = mapping(geometry)
    return {
        "type": payload["type"],
        "coordinates": _rounded_coordinates(payload["coordinates"], precision),
    }


def geometry_json(geometry: BaseGeometry, precision: int = 6) -> str:
    return json.dumps(
        geometry_mapping(geometry, precision),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_hash(*values: Any) -> str:
    payload = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def haversine_km(left: tuple[float, float], right: tuple[float, float]) -> float:
    left_lat, left_lng = map(math.radians, left)
    right_lat, right_lng = map(math.radians, right)
    latitude_delta = right_lat - left_lat
    longitude_delta = right_lng - left_lng
    value = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(left_lat) * math.cos(right_lat) * math.sin(longitude_delta / 2) ** 2
    )
    return EARTH_RADIUS_KM * 2 * math.asin(min(1.0, math.sqrt(value)))


def _cartesian(latitude: float, longitude: float) -> tuple[float, float, float]:
    lat = math.radians(latitude)
    lng = math.radians(longitude)
    return math.cos(lat) * math.cos(lng), math.cos(lat) * math.sin(lng), math.sin(lat)


def great_circle_coordinates(
    origin: tuple[float, float],
    destination: tuple[float, float],
    point_count: int = 64,
) -> list[list[float]]:
    if point_count < 2:
        point_count = 2
    start = _cartesian(*origin)
    end = _cartesian(*destination)
    dot = max(-1.0, min(1.0, sum(left * right for left, right in zip(start, end))))
    angle = math.acos(dot)
    coordinates: list[list[float]] = []
    for index in range(point_count):
        fraction = index / (point_count - 1)
        if angle < 1e-12:
            vector = start
        else:
            scale = math.sin(angle)
            left_weight = math.sin((1 - fraction) * angle) / scale
            right_weight = math.sin(fraction * angle) / scale
            vector = tuple(
                left_weight * left + right_weight * right
                for left, right in zip(start, end)
            )
        longitude = math.degrees(math.atan2(vector[1], vector[0]))
        latitude = math.degrees(math.atan2(vector[2], math.hypot(vector[0], vector[1])))
        coordinates.append([round(normalize_longitude(longitude), 6), round(latitude, 6)])
    coordinates[0] = [round(normalize_longitude(origin[1]), 6), round(origin[0], 6)]
    coordinates[-1] = [round(normalize_longitude(destination[1]), 6), round(destination[0], 6)]
    return coordinates


def geodesic_circle(latitude: float, longitude: float, radius_km: float, points: int = 48) -> BaseGeometry:
    angular_distance = radius_km / EARTH_RADIUS_KM
    center_latitude = math.radians(latitude)
    center_longitude = math.radians(longitude)
    coordinates: list[tuple[float, float]] = []
    for index in range(points):
        bearing = 2 * math.pi * index / points
        target_latitude = math.asin(
            math.sin(center_latitude) * math.cos(angular_distance)
            + math.cos(center_latitude) * math.sin(angular_distance) * math.cos(bearing)
        )
        target_longitude = center_longitude + math.atan2(
            math.sin(bearing) * math.sin(angular_distance) * math.cos(center_latitude),
            math.cos(angular_distance) - math.sin(center_latitude) * math.sin(target_latitude),
        )
        coordinates.append(
            (normalize_longitude(math.degrees(target_longitude)), math.degrees(target_latitude))
        )
    coordinates.append(coordinates[0])
    return shape({"type": "Polygon", "coordinates": [coordinates]})


def _line_parts(geometry: BaseGeometry) -> Iterable[LineString]:
    if geometry.geom_type in {"LineString", "LinearRing"}:
        yield LineString(geometry.coords)
    elif hasattr(geometry, "geoms"):
        for child in geometry.geoms:
            yield from _line_parts(child)


def line_length_km(geometry: BaseGeometry) -> float:
    total = 0.0
    for line in _line_parts(geometry):
        coordinates = list(line.coords)
        for left, right in zip(coordinates, coordinates[1:]):
            total += haversine_km((left[1], normalize_longitude(left[0])), (right[1], normalize_longitude(right[0])))
    return total


def _longitude_span(geometry: BaseGeometry) -> float:
    longitudes = [normalize_longitude(point[0]) for line in _line_parts(geometry) for point in line.coords]
    return max(longitudes) - min(longitudes) if longitudes else 0.0


def _positive_longitudes(geometry: BaseGeometry) -> BaseGeometry:
    return transform(lambda x, y, z=None: (x + 360 if x < 0 else x, y), geometry)


def geometry_exposure(route_value: Any, zone_value: Any) -> dict[str, Any] | None:
    route = parse_geojson(route_value)
    zone = parse_geojson(zone_value)
    if route is None or zone is None or route.geom_type not in {"LineString", "MultiLineString"}:
        return None
    if _longitude_span(route) > 180:
        route = _positive_longitudes(route)
        zone = _positive_longitudes(zone)
    route_distance = line_length_km(route)
    if route_distance <= 0:
        return None
    intersection = route.intersection(zone)
    intersection_distance = line_length_km(intersection)
    if intersection_distance <= 0.01:
        return None
    return {
        "intersection_distance_km": round(intersection_distance, 3),
        "route_distance_km": round(route_distance, 3),
        "exposure_ratio": round(min(1.0, intersection_distance / route_distance), 6),
    }


def land_coverage_fraction(coordinates: list[list[float]], land_value: Any) -> float:
    land = parse_geojson(land_value)
    if land is None or not coordinates:
        return 0.0
    valid_points = [
        Point(normalize_longitude(point[0]), point[1])
        for point in coordinates
        if len(point) >= 2 and valid_coordinate(point[1], point[0])
    ]
    if not valid_points:
        return 0.0
    covered = sum(1 for point in valid_points if land.covers(point))
    return round(covered / len(valid_points), 6)
