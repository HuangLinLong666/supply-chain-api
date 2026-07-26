from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import searoute
from shapely.geometry import LineString

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.neo4j_client import close_driver, get_driver
from gdelt.exposure import exposed_zone_ids
from geography.geometry import (
    GEOSPATIAL_VERSION,
    geometry_exposure,
    geometry_json,
    great_circle_coordinates,
    haversine_km,
    land_coverage_fraction,
    line_length_km,
    normalize_longitude,
    parse_geojson,
    stable_hash,
    valid_coordinate,
)
from geography.repository import (
    ensure_schema,
    list_exposures,
    list_locations,
    list_segments,
    list_zones,
    write_exposures,
    write_locations,
    write_segments,
    write_zones,
)


EXECUTE_CONFIRMATION = "APPLY_GEOSPATIAL_STAGE6"
DEFAULT_OSRM_URL = "https://router.project-osrm.org"
LAND_COVERAGE_THRESHOLD = 0.55
SUPPORTED_GEOMETRY_MODES = {"sea", "air", "rail", "road"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_mode(value: Any) -> str:
    mode = str(value or "").strip().casefold()
    return {
        "ocean": "sea",
        "maritime": "sea",
        "aviation": "air",
        "truck": "road",
    }.get(mode, mode)


def coordinate_from_catalog(
    location_id: Any,
    locations: dict[str, dict[str, Any]],
) -> tuple[float, float] | None:
    row = locations.get(str(location_id or "")) or {}
    latitude = row.get("latitude")
    longitude = row.get("longitude")
    if not valid_coordinate(latitude, longitude):
        return None
    return float(latitude), normalize_longitude(float(longitude))


def normalize_linestring_coordinates(coordinates: Any) -> list[list[float]]:
    normalized: list[list[float]] = []
    for item in coordinates or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        longitude = normalize_longitude(float(item[0]))
        latitude = float(item[1])
        if valid_coordinate(latitude, longitude):
            point = [round(longitude, 6), round(latitude, 6)]
            if not normalized or point != normalized[-1]:
                normalized.append(point)
    return normalized


def simplify_network_coordinates(
    coordinates: list[list[float]],
    tolerance_degrees: float = 0.005,
) -> list[list[float]]:
    if len(coordinates) < 3:
        return coordinates
    simplified = LineString(coordinates).simplify(
        tolerance_degrees,
        preserve_topology=False,
    )
    return normalize_linestring_coordinates(simplified.coords)


def osrm_geometry(
    origin: tuple[float, float],
    destination: tuple[float, float],
    base_url: str,
    client: httpx.Client | None = None,
) -> list[list[float]]:
    coordinates = (
        f"{origin[1]:.6f},{origin[0]:.6f};"
        f"{destination[1]:.6f},{destination[0]:.6f}"
    )
    url = f"{base_url.rstrip('/')}/route/v1/driving/{coordinates}"
    owns_client = client is None
    http_client = client or httpx.Client(
        timeout=45.0,
        follow_redirects=True,
        headers={"User-Agent": "supply-chain-api-stage6/1.0"},
    )
    try:
        response = http_client.get(
            url,
            params={"overview": "full", "geometries": "geojson", "steps": "false"},
        )
        response.raise_for_status()
        payload = response.json()
        routes = payload.get("routes") or []
        if payload.get("code") != "Ok" or not routes:
            raise RuntimeError(f"OSRM returned {payload.get('code') or 'no route'}")
        return simplify_network_coordinates(
            normalize_linestring_coordinates(routes[0]["geometry"]["coordinates"])
        )
    finally:
        if owns_client:
            http_client.close()


def empty_geometry_row(
    segment: dict[str, Any],
    *,
    generated_at: str,
    geometry_status: str,
    feasibility_status: str,
    feasibility_reason: str,
    geometry_method: str,
) -> dict[str, Any]:
    row = {
        "element_id": segment["element_id"],
        "segment_id": str(segment["segment_id"]),
        "geometry_geojson": None,
        "geometry_source": None,
        "geometry_source_url": None,
        "geometry_license": None,
        "geometry_status": geometry_status,
        "geometry_confidence": 0.0,
        "geometry_distance_km": None,
        "geometry_generated_at": generated_at,
        "geometry_method": geometry_method,
        "feasibility_status": feasibility_status,
        "feasibility_reason": feasibility_reason,
        "geospatial_version": GEOSPATIAL_VERSION,
        "updated_at": generated_at,
    }
    row["geometry_hash"] = stable_hash(
        row["geometry_geojson"],
        row["geometry_source"],
        row["geometry_status"],
        row["geometry_confidence"],
        row["geometry_method"],
        row["feasibility_status"],
        row["feasibility_reason"],
    )
    return row


def geometry_row(
    segment: dict[str, Any],
    coordinates: list[list[float]],
    *,
    generated_at: str,
    source: str,
    source_url: str,
    license_name: str,
    status: str,
    confidence: float,
    method: str,
    feasibility_status: str,
    feasibility_reason: str,
) -> dict[str, Any]:
    geometry = LineString(coordinates)
    geojson = geometry_json(geometry, precision=6)
    row = {
        "element_id": segment["element_id"],
        "segment_id": str(segment["segment_id"]),
        "geometry_geojson": geojson,
        "geometry_source": source,
        "geometry_source_url": source_url,
        "geometry_license": license_name,
        "geometry_status": status,
        "geometry_confidence": round(confidence, 4),
        "geometry_distance_km": round(line_length_km(geometry), 3),
        "geometry_generated_at": generated_at,
        "geometry_method": method,
        "feasibility_status": feasibility_status,
        "feasibility_reason": feasibility_reason,
        "geospatial_version": GEOSPATIAL_VERSION,
        "updated_at": generated_at,
    }
    row["geometry_hash"] = stable_hash(
        geojson,
        source,
        status,
        row["geometry_confidence"],
        method,
        feasibility_status,
        feasibility_reason,
    )
    return row


def preserved_geometry_row(
    segment: dict[str, Any],
    generated_at: str,
) -> dict[str, Any] | None:
    geometry_value = segment.get("geometry_geojson") or segment.get("geometry_json")
    geometry = parse_geojson(geometry_value)
    if (
        geometry is None
        or geometry.geom_type != "LineString"
        or not segment.get("geometry_hash")
    ):
        return None
    coordinates = simplify_network_coordinates(
        normalize_linestring_coordinates(geometry.coords)
    )
    return geometry_row(
        segment,
        coordinates,
        generated_at=generated_at,
        source=str(segment.get("geometry_source") or "OSRM routing over OpenStreetMap data"),
        source_url=str(segment.get("geometry_source_url") or DEFAULT_OSRM_URL),
        license_name=str(
            segment.get("geometry_license")
            or "OSRM BSD-2-Clause; OpenStreetMap ODbL 1.0"
        ),
        status=str(segment.get("geometry_status") or "estimated_road_network"),
        confidence=float(segment.get("geometry_confidence") or 0.7),
        method="osrm_driving_route_simplified_0_005deg",
        feasibility_status=str(segment.get("feasibility_status") or "network_route_available"),
        feasibility_reason=str(
            segment.get("feasibility_reason")
            or "OSRM returned a road-network path; border and carrier feasibility remain unverified"
        ),
    )


def build_segment_geometry(
    segment: dict[str, Any],
    locations: dict[str, dict[str, Any]],
    land_mask: dict[str, Any],
    generated_at: str,
    *,
    enable_osrm: bool = False,
    osrm_base_url: str = DEFAULT_OSRM_URL,
    osrm_client: httpx.Client | None = None,
) -> dict[str, Any]:
    mode = canonical_mode(segment.get("mode"))
    if str(segment.get("data_status") or "").casefold() == "synthetic":
        return empty_geometry_row(
            segment,
            generated_at=generated_at,
            geometry_status="unavailable_synthetic",
            feasibility_status="unverified_synthetic",
            feasibility_reason="Synthetic route data is not promoted to a real geometry",
            geometry_method="none",
        )
    if mode not in SUPPORTED_GEOMETRY_MODES:
        return empty_geometry_row(
            segment,
            generated_at=generated_at,
            geometry_status="unavailable_unsupported_mode",
            feasibility_status="unverified",
            feasibility_reason=f"No stage-6 geometry provider is configured for mode {mode or 'unknown'}",
            geometry_method="none",
        )
    origin = coordinate_from_catalog(segment.get("from_id"), locations)
    destination = coordinate_from_catalog(segment.get("to_id"), locations)
    if origin is None or destination is None:
        return empty_geometry_row(
            segment,
            generated_at=generated_at,
            geometry_status="unavailable_missing_coordinates",
            feasibility_status="unverified",
            feasibility_reason="Both endpoints require sourced coordinates",
            geometry_method="none",
        )
    great_circle = great_circle_coordinates(origin, destination, point_count=96)
    if mode in {"road", "rail"}:
        land_fraction = land_coverage_fraction(great_circle, land_mask["geometry_geojson"])
        origin_country = str(segment.get("from_country") or "").strip().casefold()
        destination_country = str(segment.get("to_country") or "").strip().casefold()
        same_country_short_route = (
            bool(origin_country)
            and origin_country == destination_country
            and haversine_km(origin, destination) <= 1000.0
        )
        if land_fraction < LAND_COVERAGE_THRESHOLD and not same_country_short_route:
            return empty_geometry_row(
                segment,
                generated_at=generated_at,
                geometry_status="invalid_cross_ocean",
                feasibility_status="invalid_cross_ocean",
                feasibility_reason=(
                    f"Great-circle land coverage {land_fraction:.3f} is below "
                    f"the {LAND_COVERAGE_THRESHOLD:.2f} threshold for {mode}"
                ),
                geometry_method="land_mask_feasibility_check",
            )
    if mode == "sea":
        feature = searoute.searoute(
            [origin[1], origin[0]],
            [destination[1], destination[0]],
            units="km",
        )
        coordinates = normalize_linestring_coordinates(feature["geometry"]["coordinates"])
        coordinates = [[origin[1], origin[0]], *coordinates, [destination[1], destination[0]]]
        coordinates = normalize_linestring_coordinates(coordinates)
        return geometry_row(
            segment,
            coordinates,
            generated_at=generated_at,
            source="searoute-py open sea network",
            source_url="https://github.com/genthalili/searoute-py",
            license_name="Apache-2.0",
            status="estimated_open_sea_network",
            confidence=0.65,
            method="open_sea_network_shortest_path",
            feasibility_status="estimated_routable",
            feasibility_reason="Open-source sea network estimate; not suitable for navigation",
        )
    if mode == "air":
        return geometry_row(
            segment,
            great_circle,
            generated_at=generated_at,
            source="WGS84 great-circle endpoint estimate",
            source_url="https://en.wikipedia.org/wiki/Great-circle_distance",
            license_name="Derived calculation",
            status="estimated_great_circle",
            confidence=0.5,
            method="great_circle_interpolation",
            feasibility_status="estimated_routable",
            feasibility_reason="Great-circle estimate does not represent an assigned airway or flight plan",
        )
    preserved_road_geometry = (
        preserved_geometry_row(segment, generated_at)
        if mode == "road" and segment.get("geometry_status") == "estimated_road_network"
        else None
    )
    if mode == "road" and not enable_osrm and preserved_road_geometry is not None:
        return preserved_road_geometry
    if mode == "road" and enable_osrm:
        try:
            coordinates = osrm_geometry(
                origin,
                destination,
                osrm_base_url,
                client=osrm_client,
            )
            if len(coordinates) >= 2:
                return geometry_row(
                    segment,
                    coordinates,
                    generated_at=generated_at,
                    source="OSRM routing over OpenStreetMap data",
                    source_url=osrm_base_url,
                    license_name="OSRM BSD-2-Clause; OpenStreetMap ODbL 1.0",
                    status="estimated_road_network",
                    confidence=0.7,
                    method="osrm_driving_route_simplified_0_005deg",
                    feasibility_status="network_route_available",
                    feasibility_reason="OSRM returned a road-network path; border and carrier feasibility remain unverified",
                )
        except Exception as exc:
            if preserved_road_geometry is not None:
                return preserved_road_geometry
            failure = f"OSRM unavailable: {type(exc).__name__}: {exc}"
        else:
            failure = "OSRM returned no usable LineString"
    elif mode == "road":
        failure = "OSRM lookup was not enabled"
    else:
        failure = "No trusted rail-network geometry provider is configured"
    return empty_geometry_row(
        segment,
        generated_at=generated_at,
        geometry_status="estimated_endpoint_fallback",
        feasibility_status="unverified",
        feasibility_reason=failure,
        geometry_method="endpoint_inference_only",
    )


def build_location_rows(
    existing: list[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    updated_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    all_rows: list[dict[str, Any]] = []
    writes: list[dict[str, Any]] = []
    for current in existing:
        location_id = str(current["location_id"])
        reference = dict(catalog.get(location_id) or {})
        if not reference:
            continue
        if reference.get("latitude") is None and valid_coordinate(
            current.get("latitude"), current.get("longitude")
        ):
            reference.update(
                {
                    "latitude": float(current["latitude"]),
                    "longitude": normalize_longitude(float(current["longitude"])),
                    "coordinate_source": current.get("coordinate_source")
                    or "legacy database coordinate (unverified)",
                    "coordinate_source_url": current.get("coordinate_source_url"),
                    "coordinate_license": current.get("coordinate_license"),
                    "coordinate_collected_at": current.get("coordinate_collected_at"),
                    "coordinate_confidence": min(
                        0.15, float(current.get("coordinate_confidence") or 0.15)
                    ),
                    "coordinate_status": "legacy_unverified",
                    "coordinate_record_id": current.get("coordinate_record_id"),
                    "missing_reason": "reference_match_missing_legacy_coordinate_preserved",
                }
            )
            reference["coordinate_hash"] = stable_hash(
                reference["latitude"],
                reference["longitude"],
                reference["coordinate_source"],
                reference["coordinate_status"],
                reference["coordinate_record_id"],
                reference.get("canonical_unlocode"),
                reference.get("identity_status"),
            )
        row = {
            **reference,
            "element_id": current["element_id"],
            "geospatial_version": GEOSPATIAL_VERSION,
            "updated_at": updated_at,
        }
        all_rows.append(row)
        if current.get("coordinate_hash") != row["coordinate_hash"]:
            writes.append(row)
    return all_rows, writes


def build_zone_rows(
    existing: list[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    updated_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    existing_hashes = {str(row["zone_id"]): row.get("geometry_hash") for row in existing}
    all_rows: list[dict[str, Any]] = []
    writes: list[dict[str, Any]] = []
    for zone_id, reference in sorted(catalog.items()):
        row = {
            **reference,
            "geometry_geojson": json.dumps(
                reference["geometry_geojson"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "geospatial_version": GEOSPATIAL_VERSION,
            "updated_at": updated_at,
        }
        all_rows.append(row)
        if existing_hashes.get(zone_id) != row["geometry_hash"]:
            writes.append(row)
    return all_rows, writes


def build_exposure_rows(
    segments: list[dict[str, Any]],
    segment_geometries: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    risk_zones: list[dict[str, Any]],
    existing: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    geometry_by_element = {row["element_id"]: row for row in segment_geometries}
    zone_by_id = {str(row["zone_id"]): row for row in zones}
    existing_by_key = {
        (str(row["element_id"]), str(row["zone_id"])): row
        for row in existing
    }
    desired: list[dict[str, Any]] = []
    for segment in segments:
        geometry = geometry_by_element[segment["element_id"]]
        mode = canonical_mode(segment.get("mode"))
        geometry_status = str(geometry.get("geometry_status") or "unavailable")
        if geometry_status == "invalid_cross_ocean":
            continue
        has_geometry = parse_geojson(geometry.get("geometry_geojson")) is not None
        if has_geometry:
            for zone_id, zone in zone_by_id.items():
                if mode not in set(zone.get("applicable_modes") or []):
                    continue
                exposure = geometry_exposure(
                    geometry["geometry_geojson"],
                    zone["geometry_geojson"],
                )
                if exposure is None:
                    continue
                confidence = round(
                    min(
                        float(geometry.get("geometry_confidence") or 0.0),
                        float(zone.get("geometry_confidence") or 0.0),
                    ),
                    4,
                )
                row = {
                    "element_id": segment["element_id"],
                    "segment_id": str(segment["segment_id"]),
                    "zone_id": zone_id,
                    "exposure_method": "geometry_intersection",
                    "intersection_distance_km": exposure["intersection_distance_km"],
                    "route_distance_km": exposure["route_distance_km"],
                    "exposure_ratio": exposure["exposure_ratio"],
                    "confidence": confidence,
                    "geometry_status": geometry_status,
                    "geospatial_version": GEOSPATIAL_VERSION,
                }
                row["exposure_hash"] = stable_hash(
                    row["segment_id"],
                    row["zone_id"],
                    row["exposure_method"],
                    row["intersection_distance_km"],
                    row["route_distance_km"],
                    row["exposure_ratio"],
                    row["confidence"],
                    geometry.get("geometry_hash"),
                    zone.get("geometry_hash"),
                )
                desired.append(row)
        else:
            inferred = exposed_zone_ids(segment, risk_zones)
            for zone_id in inferred:
                zone = zone_by_id.get(zone_id)
                if zone is None or mode not in set(zone.get("applicable_modes") or []):
                    continue
                row = {
                    "element_id": segment["element_id"],
                    "segment_id": str(segment["segment_id"]),
                    "zone_id": zone_id,
                    "exposure_method": "inferred_from_endpoints",
                    "intersection_distance_km": None,
                    "route_distance_km": None,
                    "exposure_ratio": None,
                    "confidence": 0.2,
                    "geometry_status": geometry_status,
                    "geospatial_version": GEOSPATIAL_VERSION,
                }
                row["exposure_hash"] = stable_hash(
                    row["segment_id"],
                    row["zone_id"],
                    row["exposure_method"],
                    row["confidence"],
                    geometry.get("geometry_hash"),
                )
                desired.append(row)
    desired_by_key = {
        (str(row["element_id"]), str(row["zone_id"])): row
        for row in desired
    }
    writes = [
        row
        for key, row in desired_by_key.items()
        if (existing_by_key.get(key) or {}).get("exposure_hash") != row["exposure_hash"]
        or not bool((existing_by_key.get(key) or {}).get("active"))
    ]
    deactivations = [
        {"element_id": key[0], "segment_id": str(row["segment_id"]), "zone_id": key[1]}
        for key, row in existing_by_key.items()
        if bool(row.get("active")) and key not in desired_by_key
    ]
    return list(desired_by_key.values()), writes, deactivations


def build_migration_plan(
    *,
    reference: dict[str, Any],
    locations: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    zones: list[dict[str, Any]],
    exposures: list[dict[str, Any]],
    risk_zone_config: dict[str, Any],
    generated_at: str,
    enable_osrm: bool = False,
    osrm_base_url: str = DEFAULT_OSRM_URL,
    segment_builder: Callable[..., dict[str, Any]] = build_segment_geometry,
) -> dict[str, Any]:
    all_locations, location_writes = build_location_rows(
        locations,
        reference["locations"],
        generated_at,
    )
    location_catalog = {row["location_id"]: row for row in all_locations}
    segment_rows = [
        segment_builder(
            segment,
            location_catalog,
            reference["land_mask"],
            generated_at,
            enable_osrm=enable_osrm,
            osrm_base_url=osrm_base_url,
        )
        for segment in segments
    ]
    current_segment_hashes = {
        str(row["element_id"]): row.get("geometry_hash") for row in segments
    }
    segment_writes = [
        row
        for row in segment_rows
        if current_segment_hashes.get(str(row["element_id"])) != row["geometry_hash"]
    ]
    all_zones, zone_writes = build_zone_rows(
        zones,
        reference["zones"],
        generated_at,
    )
    desired_exposures, exposure_writes, exposure_deactivations = build_exposure_rows(
        segments,
        segment_rows,
        all_zones,
        risk_zone_config["zones"],
        exposures,
    )
    return {
        "all_locations": all_locations,
        "location_writes": location_writes,
        "all_segment_rows": segment_rows,
        "segment_writes": segment_writes,
        "all_zone_rows": all_zones,
        "zone_writes": zone_writes,
        "desired_exposures": desired_exposures,
        "exposure_writes": exposure_writes,
        "exposure_deactivations": exposure_deactivations,
    }


def plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "location_updates": len(plan["location_writes"]),
        "coordinate_statuses": dict(
            sorted(Counter(row["coordinate_status"] for row in plan["all_locations"]).items())
        ),
        "segment_updates": len(plan["segment_writes"]),
        "geometry_statuses": dict(
            sorted(Counter(row["geometry_status"] for row in plan["all_segment_rows"]).items())
        ),
        "zone_updates": len(plan["zone_writes"]),
        "zones_with_geometry": len(plan["all_zone_rows"]),
        "desired_passes_through": len(plan["desired_exposures"]),
        "passes_through_updates": len(plan["exposure_writes"]),
        "passes_through_deactivations": len(plan["exposure_deactivations"]),
        "exposure_methods": dict(
            sorted(Counter(row["exposure_method"] for row in plan["desired_exposures"]).items())
        ),
    }


def compact_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "locations": [
            {
                key: row.get(key)
                for key in (
                    "location_id",
                    "coordinate_source",
                    "coordinate_status",
                    "coordinate_confidence",
                    "canonical_unlocode",
                    "identity_status",
                )
            }
            for row in plan["location_writes"]
        ],
        "segments": [
            {
                key: row.get(key)
                for key in (
                    "segment_id",
                    "geometry_source",
                    "geometry_status",
                    "geometry_confidence",
                    "geometry_distance_km",
                    "feasibility_status",
                    "feasibility_reason",
                )
            }
            for row in plan["segment_writes"]
        ],
        "zones": [
            {
                key: row.get(key)
                for key in (
                    "zone_id",
                    "geometry_source",
                    "geometry_status",
                    "geometry_confidence",
                    "applicable_modes",
                )
            }
            for row in plan["zone_writes"]
        ],
        "passes_through": [
            {
                key: row.get(key)
                for key in (
                    "segment_id",
                    "zone_id",
                    "exposure_method",
                    "intersection_distance_km",
                    "exposure_ratio",
                    "confidence",
                    "geometry_status",
                )
            }
            for row in plan["exposure_writes"]
        ],
        "passes_through_deactivations": plan["exposure_deactivations"],
    }


def render_report(artifact: dict[str, Any], json_path: Path) -> str:
    summary = artifact["summary"]
    lines = [
        "# 阶段 6 地理数据迁移报告",
        "",
        f"- 模式：`{artifact['mode']}`",
        f"- 状态：`{artifact['execution']['status']}`",
        f"- 生成时间：`{artifact['metadata']['generated_at_utc']}`",
        f"- 完整 JSON：`{json_path}`",
        "- 节点删除：`0`；关系删除：`0`。旧关系仅在本集成范围内软停用。",
        "",
        "## 计划统计",
        "",
        f"- 地点属性更新：{summary['location_updates']}。",
        f"- 路段几何/可行性更新：{summary['segment_updates']}。",
        f"- 风险区更新：{summary['zone_updates']}。",
        f"- 目标 `PASSES_THROUGH`：{summary['desired_passes_through']}。",
        f"- `PASSES_THROUGH` 写入：{summary['passes_through_updates']}；软停用：{summary['passes_through_deactivations']}。",
        "",
        "## 置信度边界",
        "",
        "- `geometry_intersection` 表示路线几何与风险区几何真实相交，但几何本身仍可能是估算。",
        "- `inferred_from_endpoints` 仅是低置信度回退，不能当作实际经过证明。",
        "- searoute-py、Great Circle 与小比例尺 Natural Earth 均不可用于导航。",
        "- 跨洋公路/铁路会标记 `invalid_cross_ocean`，不会进入推荐图。",
        "",
        "## 执行命令",
        "",
        "```bash",
        "python scripts/migrate_geospatial_data.py --dry-run",
        f"python scripts/migrate_geospatial_data.py --execute --confirm {EXECUTE_CONFIRMATION} --enable-osrm",
        "```",
        "",
    ]
    return "\n".join(lines)


def write_artifacts(
    artifact: dict[str, Any],
    output_dir: Path,
    generated_at: datetime,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"geospatial_migration_{timestamp}.json"
    markdown_path = output_dir / f"geospatial_migration_{timestamp}.md"
    json_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_report(artifact, json_path), encoding="utf-8")
    return json_path, markdown_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="阶段 6：补齐坐标、路线几何与风险区暴露关系")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只生成迁移计划（默认）")
    mode.add_argument("--execute", action="store_true", help="执行幂等写入")
    parser.add_argument("--confirm", default="", help=f"执行时必须填写 {EXECUTE_CONFIRMATION}")
    parser.add_argument(
        "--reference",
        type=Path,
        default=Path("config/geospatial_reference.json"),
        help="地理参考目录",
    )
    parser.add_argument(
        "--risk-zones",
        type=Path,
        default=Path("config/gdelt_risk_zones.json"),
        help="GDELT 风险区配置",
    )
    parser.add_argument("--enable-osrm", action="store_true", help="为非跨洋公路调用 OSRM")
    parser.add_argument("--osrm-url", default=DEFAULT_OSRM_URL)
    parser.add_argument("--max-node-updates", type=int, default=1000)
    parser.add_argument("--max-relationship-updates", type=int, default=5000)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    if args.execute and args.confirm != EXECUTE_CONFIRMATION:
        parser.error(f"--execute 必须同时提供 --confirm {EXECUTE_CONFIRMATION}")
    if args.max_node_updates < 0 or args.max_relationship_updates < 0:
        parser.error("迁移上限不能小于 0")
    return args


def main() -> int:
    args = parse_args()
    generated_at = utc_now()
    generated_at_text = generated_at.isoformat()
    mode = "execute" if args.execute else "dry-run"
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    risk_zone_config = json.loads(args.risk_zones.read_text(encoding="utf-8"))
    artifact: dict[str, Any] = {}
    try:
        get_driver().verify_connectivity()
        current_locations = list_locations()
        current_segments = list_segments()
        current_zones = list_zones()
        current_exposures = list_exposures()
        plan = build_migration_plan(
            reference=reference,
            locations=current_locations,
            segments=current_segments,
            zones=current_zones,
            exposures=current_exposures,
            risk_zone_config=risk_zone_config,
            generated_at=generated_at_text,
            enable_osrm=args.enable_osrm,
            osrm_base_url=args.osrm_url,
        )
        summary = plan_summary(plan)
        node_updates = (
            summary["location_updates"]
            + summary["segment_updates"]
            + summary["zone_updates"]
        )
        relationship_updates = (
            summary["passes_through_updates"]
            + summary["passes_through_deactivations"]
        )
        artifact = {
            "metadata": {
                "generated_at_utc": generated_at_text,
                "geospatial_version": GEOSPATIAL_VERSION,
                "reference_generated_at": reference.get("generated_at"),
                "credentials_redacted": True,
                "plan_sha256": stable_hash(summary, compact_plan(plan)),
            },
            "mode": mode,
            "safety": {
                "nodes_deleted": False,
                "relationships_deleted": False,
                "uses_merge": True,
                "scoped_soft_deactivation_only": True,
                "synthetic_geometry_promoted": False,
                "max_node_updates": args.max_node_updates,
                "max_relationship_updates": args.max_relationship_updates,
            },
            "before": {
                "transport_locations": len(current_locations),
                "route_segments": len(current_segments),
                "geo_zones": len(current_zones),
                "owned_passes_through": len(current_exposures),
            },
            "source_catalog": reference.get("source_catalog") or {},
            "summary": summary,
            "plan": compact_plan(plan),
            "execution": {
                "status": "dry_run" if not args.execute else "planned",
                "location_updates": 0,
                "segment_updates": 0,
                "zone_updates": 0,
                "passes_through_updates": 0,
                "passes_through_deactivations": 0,
            },
        }
        json_path, markdown_path = write_artifacts(artifact, args.output_dir, generated_at)
        if args.execute:
            if node_updates > args.max_node_updates:
                raise RuntimeError(
                    f"计划节点更新 {node_updates} 超过 --max-node-updates {args.max_node_updates}"
                )
            if relationship_updates > args.max_relationship_updates:
                raise RuntimeError(
                    "计划关系更新 "
                    f"{relationship_updates} 超过 --max-relationship-updates "
                    f"{args.max_relationship_updates}"
                )
            ensure_schema()
            location_count = write_locations(plan["location_writes"])
            zone_count = write_zones(plan["zone_writes"])
            segment_count = write_segments(plan["segment_writes"])
            exposure_counts = write_exposures(
                plan["exposure_writes"],
                plan["exposure_deactivations"],
                generated_at_text,
            )
            artifact["execution"].update(
                {
                    "status": "completed",
                    "location_updates": location_count,
                    "segment_updates": segment_count,
                    "zone_updates": zone_count,
                    "passes_through_updates": exposure_counts["written"],
                    "passes_through_deactivations": exposure_counts["deactivated"],
                }
            )
            json_path, markdown_path = write_artifacts(artifact, args.output_dir, generated_at)
    except Exception as exc:
        if artifact:
            artifact["execution"].update({"status": "failed", "error": str(exc)})
            json_path, markdown_path = write_artifacts(artifact, args.output_dir, generated_at)
        raise
    finally:
        close_driver()
    print(
        json.dumps(
            {
                "mode": mode,
                "status": artifact["execution"]["status"],
                "summary": artifact["summary"],
                "jsonArtifact": str(json_path),
                "markdownArtifact": str(markdown_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
