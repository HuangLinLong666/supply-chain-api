from __future__ import annotations

from typing import Any

from database.neo4j_client import run_query


GEOSPATIAL_INTEGRATION_ID = "geospatial-exposure-v1"


def ensure_schema() -> None:
    for statement in (
        "CREATE CONSTRAINT unified_geo_zone_id_unique IF NOT EXISTS FOR (n:GeoZone) REQUIRE n.zone_id IS UNIQUE",
        "CREATE INDEX transport_location_coordinate_status IF NOT EXISTS FOR (n:TransportLocation) ON (n.coordinate_status)",
        "CREATE INDEX route_segment_geometry_status IF NOT EXISTS FOR (n:RouteSegment) ON (n.geometry_status)",
        "CREATE INDEX geo_zone_geometry_status IF NOT EXISTS FOR (n:GeoZone) ON (n.geometry_status)",
    ):
        run_query(statement)


def list_locations() -> list[dict[str, Any]]:
    return run_query(
        """
        MATCH (location:TransportLocation)
        RETURN elementId(location) AS element_id,
               labels(location) AS labels,
               coalesce(location.location_id,location.unlocode,location.iata,location.icao,location.code,location.id) AS location_id,
               location.unlocode AS unlocode,location.canonical_unlocode AS canonical_unlocode,
               location.identity_status AS identity_status,
               location.iata AS iata,location.icao AS icao,
               coalesce(location.name_zh,location.name_en,location.name) AS name,
               location.city AS city,location.country AS country,location.country_code AS country_code,
               location.latitude AS latitude,location.longitude AS longitude,
               location.coordinate_source AS coordinate_source,
               location.coordinate_source_url AS coordinate_source_url,
               location.coordinate_license AS coordinate_license,
               location.coordinate_collected_at AS coordinate_collected_at,
               location.coordinate_confidence AS coordinate_confidence,
               location.coordinate_status AS coordinate_status,
               location.coordinate_record_id AS coordinate_record_id,
               location.coordinate_hash AS coordinate_hash,
               location.data_status AS data_status
        ORDER BY location_id
        """
    )


def list_segments() -> list[dict[str, Any]]:
    return run_query(
        """
        MATCH (segment:RouteSegment)
        OPTIONAL MATCH (segment)-[:FROM_NODE]->(from_node)
        OPTIONAL MATCH (segment)-[:TO_NODE]->(to_node)
        WITH segment,head(collect(DISTINCT from_node)) AS origin,head(collect(DISTINCT to_node)) AS destination
        RETURN elementId(segment) AS element_id,
               coalesce(segment.segment_id,segment.segmentId,elementId(segment)) AS segment_id,
               coalesce(segment.canonical_mode,segment.mode,segment.routeMode) AS mode,
               segment.data_status AS data_status,segment.source_type AS source_type,
               coalesce(segment.is_inferred,false) AS is_inferred,
               segment.feasibility_status AS feasibility_status,
               segment.geometry_geojson AS geometry_geojson,
               segment.geometry_json AS geometry_json,
               segment.geometry_source AS geometry_source,
               segment.geometry_source_url AS geometry_source_url,
               segment.geometry_license AS geometry_license,
               segment.geometry_status AS geometry_status,
               segment.geometry_confidence AS geometry_confidence,
               segment.geometry_distance_km AS geometry_distance_km,
               segment.geometry_generated_at AS geometry_generated_at,
               segment.geometry_method AS geometry_method,
               segment.geometry_hash AS geometry_hash,
               segment.feasibility_reason AS feasibility_reason,
               segment.geospatial_version AS geospatial_version,
               coalesce(origin.location_id,origin.unlocode,origin.iata,origin.code,origin.id,elementId(origin)) AS from_id,
               coalesce(origin.name_zh,origin.name_en,origin.name,origin.city) AS from_name,
               origin.city AS from_city,origin.country AS from_country,
               origin.latitude AS from_lat,origin.longitude AS from_lng,
               coalesce(destination.location_id,destination.unlocode,destination.iata,destination.code,destination.id,elementId(destination)) AS to_id,
               coalesce(destination.name_zh,destination.name_en,destination.name,destination.city) AS to_name,
               destination.city AS to_city,destination.country AS to_country,
               destination.latitude AS to_lat,destination.longitude AS to_lng
        ORDER BY segment_id
        """
    )


def list_zones() -> list[dict[str, Any]]:
    return run_query(
        """
        MATCH (zone:GeoZone)
        RETURN zone.zone_id AS zone_id,zone.name AS name,zone.zone_type AS zone_type,
               zone.geometry_geojson AS geometry_geojson,
               zone.geometry_source AS geometry_source,
               zone.geometry_source_url AS geometry_source_url,
               zone.geometry_license AS geometry_license,
               zone.geometry_status AS geometry_status,
               zone.geometry_confidence AS geometry_confidence,
               zone.geometry_hash AS geometry_hash,
               zone.applicable_modes AS applicable_modes
        ORDER BY zone_id
        """
    )


def list_exposures() -> list[dict[str, Any]]:
    return run_query(
        """
        MATCH (segment:RouteSegment)-[exposure:PASSES_THROUGH]->(zone:GeoZone)
        WHERE exposure.integration_id=$integration_id
        RETURN elementId(segment) AS element_id,
               coalesce(segment.segment_id,segment.segmentId,elementId(segment)) AS segment_id,
               zone.zone_id AS zone_id,exposure.active AS active,
               exposure.exposure_hash AS exposure_hash,
               exposure.exposure_method AS exposure_method
        """,
        {"integration_id": GEOSPATIAL_INTEGRATION_ID},
    )


def write_locations(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    result = run_query(
        """
        UNWIND $rows AS row
        MATCH (location:TransportLocation) WHERE elementId(location)=row.element_id
        SET location.latitude=row.latitude,location.longitude=row.longitude,
            location.coordinate_source=row.coordinate_source,
            location.coordinate_source_url=row.coordinate_source_url,
            location.coordinate_license=row.coordinate_license,
            location.coordinate_collected_at=CASE WHEN row.coordinate_collected_at IS NULL THEN null ELSE datetime(row.coordinate_collected_at) END,
            location.coordinate_confidence=row.coordinate_confidence,
            location.coordinate_status=row.coordinate_status,
            location.coordinate_record_id=row.coordinate_record_id,
            location.coordinate_hash=row.coordinate_hash,
            location.canonical_unlocode=coalesce(row.canonical_unlocode,location.canonical_unlocode),
            location.identity_status=coalesce(row.identity_status,location.identity_status),
            location.geospatial_version=$version,
            location.geospatial_updated_at=datetime($updated_at)
        RETURN count(location) AS updated
        """,
        {"rows": rows, "version": rows[0]["geospatial_version"], "updated_at": rows[0]["updated_at"]},
    )
    return int(result[0]["updated"] if result else 0)


def write_segments(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    result = run_query(
        """
        UNWIND $rows AS row
        MATCH (segment:RouteSegment) WHERE elementId(segment)=row.element_id
        SET segment.geometry_geojson=row.geometry_geojson,
            segment.geometry_json=row.geometry_geojson,
            segment.geometry_source=row.geometry_source,
            segment.geometry_source_url=row.geometry_source_url,
            segment.geometry_license=row.geometry_license,
            segment.geometry_status=row.geometry_status,
            segment.geometry_confidence=row.geometry_confidence,
            segment.geometry_hash=row.geometry_hash,
            segment.geometry_distance_km=row.geometry_distance_km,
            segment.geometry_generated_at=CASE WHEN row.geometry_generated_at IS NULL THEN null ELSE datetime(row.geometry_generated_at) END,
            segment.geometry_method=row.geometry_method,
            segment.geometry_is_navigational=false,
            segment.feasibility_status=row.feasibility_status,
            segment.feasibility_reason=row.feasibility_reason,
            segment.geospatial_version=$version,
            segment.geospatial_updated_at=datetime($updated_at)
        RETURN count(segment) AS updated
        """,
        {"rows": rows, "version": rows[0]["geospatial_version"], "updated_at": rows[0]["updated_at"]},
    )
    return int(result[0]["updated"] if result else 0)


def write_zones(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    result = run_query(
        """
        UNWIND $rows AS row
        MERGE (zone:GeoZone {zone_id:row.zone_id})
        SET zone.name=row.name,zone.zone_type=row.zone_type,
            zone.geometry_geojson=row.geometry_geojson,
            zone.geometry_source=row.geometry_source,
            zone.geometry_source_url=row.geometry_source_url,
            zone.geometry_license=row.geometry_license,
            zone.geometry_status=row.geometry_status,
            zone.geometry_confidence=row.geometry_confidence,
            zone.geometry_collected_at=datetime(row.geometry_collected_at),
            zone.geometry_hash=row.geometry_hash,
            zone.applicable_modes=row.applicable_modes,
            zone.geometry_is_navigational=false,
            zone.geospatial_version=$version,
            zone.geospatial_updated_at=datetime($updated_at)
        RETURN count(zone) AS updated
        """,
        {"rows": rows, "version": rows[0]["geospatial_version"], "updated_at": rows[0]["updated_at"]},
    )
    return int(result[0]["updated"] if result else 0)


def write_exposures(rows: list[dict[str, Any]], deactivations: list[dict[str, str]], updated_at: str) -> dict[str, int]:
    deactivated = 0
    if deactivations:
        result = run_query(
            """
            UNWIND $rows AS row
            MATCH (segment:RouteSegment)-[exposure:PASSES_THROUGH]->(zone:GeoZone)
            WHERE elementId(segment)=row.element_id
              AND zone.zone_id=row.zone_id AND exposure.integration_id=$integration_id
            SET exposure.active=false,exposure.deactivated_at=datetime($updated_at)
            RETURN count(exposure) AS updated
            """,
            {
                "rows": deactivations,
                "integration_id": GEOSPATIAL_INTEGRATION_ID,
                "updated_at": updated_at,
            },
        )
        deactivated = int(result[0]["updated"] if result else 0)
    written = 0
    if rows:
        result = run_query(
            """
            UNWIND $rows AS row
            MATCH (segment:RouteSegment) WHERE elementId(segment)=row.element_id
            MATCH (zone:GeoZone {zone_id:row.zone_id})
            MERGE (segment)-[exposure:PASSES_THROUGH {integration_id:$integration_id}]->(zone)
            SET exposure.active=true,exposure.exposure_method=row.exposure_method,
                exposure.intersection_distance_km=row.intersection_distance_km,
                exposure.route_distance_km=row.route_distance_km,
                exposure.exposure_ratio=row.exposure_ratio,
                exposure.confidence=row.confidence,
                exposure.geometry_status=row.geometry_status,
                exposure.exposure_hash=row.exposure_hash,
                exposure.calculated_at=datetime($updated_at),
                exposure.geospatial_version=$version
            REMOVE exposure.deactivated_at
            RETURN count(exposure) AS updated
            """,
            {
                "rows": rows,
                "integration_id": GEOSPATIAL_INTEGRATION_ID,
                "updated_at": updated_at,
                "version": rows[0]["geospatial_version"],
            },
        )
        written = int(result[0]["updated"] if result else 0)
    return {"written": written, "deactivated": deactivated}
