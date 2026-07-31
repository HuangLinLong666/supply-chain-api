"""Neo4j persistence for port weather state and snapshots."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.provider_risk import build_segment_signals, calculate_provider_risk, database_risk_properties, is_fresh, parse_datetime
from app.vehicle_network.core import load_strategy
from database.neo4j_client import get_driver, get_settings, run_query
from database.unified_schema import IDENTITY_CONSTRAINTS, QUERY_INDEXES, SCHEMA_VERSION


def list_ports(port_ids: list[str] | None = None) -> list[dict[str, Any]]:
    return run_query("""
    MATCH (p:Port)
    WITH p, coalesce(p.location_id,p.unlocode,p.code,elementId(p)) AS port_id
    WHERE $port_ids IS NULL OR port_id IN $port_ids
       OR any(alias IN coalesce(p.location_aliases,[]) WHERE alias IN $port_ids)
    RETURN elementId(p) AS element_id, port_id, p.name AS name, p.city AS city, p.country AS country,
           p.iso2 AS country_code, p.latitude AS latitude, p.longitude AS longitude,
           p.weather_updated_at AS weather_updated_at
    ORDER BY name
    """, {"port_ids": port_ids})


def list_route_segments(segment_ids: list[str] | None = None) -> list[dict[str, Any]]:
    return run_query(
        """
        MATCH (segment:RouteSegment)-[:FROM_NODE]->(origin)
        MATCH (segment)-[:TO_NODE]->(destination)
        OPTIONAL MATCH (route:Route)-[:HAS_SEGMENT|HAS_LEG]->(segment)
        WITH segment,origin,destination,
             coalesce(segment.segment_id,segment.segmentId,elementId(segment)) AS segment_id,
             toLower(toString(coalesce(segment.canonical_mode,segment.mode,segment.routeMode,''))) AS mode,
             properties(segment) AS segment_properties,
             [item IN collect(DISTINCT route) WHERE item.deleted_at IS NULL] AS active_routes
        WHERE mode IN ['sea','air','rail','road']
          AND coalesce(segment.feasibility_status,'') <> 'invalid_cross_ocean'
          AND ($segment_ids IS NULL OR segment_id IN $segment_ids)
        RETURN elementId(segment) AS element_id,segment_id,mode,
               origin.latitude AS from_lat,origin.longitude AS from_lng,
               destination.latitude AS to_lat,destination.longitude AS to_lng,
               origin.country AS from_country,coalesce(origin.iso2,origin.country_code) AS from_country_code,
               labels(origin) AS from_labels,
               destination.country AS to_country,coalesce(destination.iso2,destination.country_code) AS to_country_code,
               labels(destination) AS to_labels,
               segment.data_status AS data_status,segment.source_type AS source_type,
               coalesce(segment.is_inferred,false) AS is_inferred,size(active_routes) AS active_route_count,
               coalesce(segment_properties.geometry_geojson,segment_properties.geometry_json,segment_properties.route_geometry_json,segment_properties.geojson) AS geometry,
               segment.geometry_source AS geometry_source,
               segment.geometry_status AS geometry_status,
               segment.geometry_confidence AS geometry_confidence,
               segment.feasibility_status AS feasibility_status,
               coalesce(
                 segment_properties.estimated_time_days * 24.0,
                 segment_properties.estimatedTimeHours,
                 segment_properties.duration_hours,
                 segment_properties.durationDays * 24.0,
                 segment_properties.duration_days * 24.0
               ) AS duration_hours,
               coalesce(segment.distance_km,segment.distanceKm) AS distance_km
        ORDER BY segment_id
        """,
        {"segment_ids": segment_ids},
    )


def ensure_schema() -> None:
    statements = [
        "CREATE CONSTRAINT weather_snapshot_id IF NOT EXISTS FOR (w:WeatherRiskSnapshot) REQUIRE w.snapshot_id IS UNIQUE",
        "CREATE CONSTRAINT route_weather_snapshot_id IF NOT EXISTS FOR (w:RouteWeatherRiskSnapshot) REQUIRE w.snapshot_id IS UNIQUE",
        "CREATE INDEX port_weather_risk_level IF NOT EXISTS FOR (p:Port) ON (p.weather_risk_level)",
        "CREATE INDEX port_weather_updated_at IF NOT EXISTS FOR (p:Port) ON (p.weather_updated_at)",
        "CREATE INDEX route_weather_snapshot_observed IF NOT EXISTS FOR (w:RouteWeatherRiskSnapshot) ON (w.observed_at)",
        *[
            rule.statement
            for rule in (*IDENTITY_CONSTRAINTS, *QUERY_INDEXES)
            if rule.label == "RiskObservation"
        ],
    ]
    settings = get_settings(); options = {"database": settings.database} if settings.database else {}
    with get_driver().session(**options) as session:
        for statement in statements: session.run(statement).consume()


def recalculate_segment_risk(segment_ids: list[str]) -> int:
    if not segment_ids:
        return 0
    now = datetime.now(timezone.utc)
    segments = run_query(
        """
        MATCH (segment:RouteSegment)
        WHERE elementId(segment) IN $segment_ids
        OPTIONAL MATCH (segment)-[:EXPOSED_TO_NEWS_RISK]->(zone:NewsRiskZone)
        RETURN elementId(segment) AS element_id,properties(segment) AS properties,
               [item IN collect(DISTINCT {zone_id:zone.zone_id,provider:zone.provider}) WHERE item.zone_id IS NOT NULL] AS zones
        """,
        {"segment_ids": segment_ids},
    )
    rows: list[dict[str, Any]] = []
    strategy = load_strategy()
    for segment in segments:
        properties = segment["properties"]
        news_expires_at = parse_datetime(properties.get("news_risk_expires_at"))
        zones = [
            item["zone_id"]
            for item in segment.get("zones") or []
            if item.get("zone_id") and str(item.get("provider") or "").casefold() == "gdelt"
        ]
        news_active = (
            str(properties.get("news_risk_provider") or "").casefold() == "gdelt"
            and news_expires_at is not None
            and news_expires_at > now
            and bool(zones)
        )
        route_weather_expires_at = parse_datetime(properties.get("route_weather_expires_at"))
        weather_active = str(properties.get("route_weather_provider") or "").casefold() in {
            "open-meteo",
            "open meteo",
        } and (
            route_weather_expires_at > now
            if route_weather_expires_at is not None
            else is_fresh(properties.get("route_weather_updated_at"), now=now, max_age_hours=6)
        )
        weather_observed_at = parse_datetime(properties.get("route_weather_updated_at"))
        congestion_expires_at = parse_datetime(properties.get("ais_congestion_expires_at"))
        congestion_evidence = properties.get("ais_congestion_snapshot_ids") or []
        congestion_active = (
            str(properties.get("ais_congestion_provider") or "").casefold() in {"aisstream", "aisstream.io"}
            and properties.get("ais_congestion_status") == "available"
            and congestion_expires_at is not None
            and congestion_expires_at > now
            and bool(congestion_evidence)
        )
        mode = properties.get("canonical_mode") or properties.get("mode") or properties.get("routeMode")
        signals = build_segment_signals(
            mode,
            news_score=properties.get("news_risk_score") if news_active else None,
            news_provider="GDELT" if news_active else None,
            news_observed_at=properties.get("news_risk_updated_at") if news_active else None,
            news_expires_at=properties.get("news_risk_expires_at") if news_active else None,
            news_confidence=properties.get("news_risk_confidence") if news_active else None,
            news_evidence=zones,
            weather_score=properties.get("route_weather_risk") if weather_active else None,
            weather_provider="Open-Meteo" if weather_active else None,
            weather_observed_at=properties.get("route_weather_updated_at") if weather_active else None,
            weather_expires_at=(
                route_weather_expires_at.isoformat()
                if weather_active and route_weather_expires_at
                else (weather_observed_at + timedelta(hours=6)).isoformat()
                if weather_active and weather_observed_at
                else None
            ),
            weather_confidence=properties.get("route_weather_confidence") if weather_active else None,
            weather_evidence=properties.get("route_weather_evidence") or [],
            congestion_score=properties.get("ais_congestion_score") if congestion_active else None,
            congestion_provider="AISStream.io" if congestion_active else None,
            congestion_observed_at=properties.get("ais_congestion_observed_at") if congestion_active else None,
            congestion_expires_at=properties.get("ais_congestion_expires_at") if congestion_active else None,
            congestion_confidence=properties.get("ais_congestion_confidence") if congestion_active else None,
            congestion_evidence=congestion_evidence,
        )
        risk = calculate_provider_risk(mode, signals, strategy)
        rows.append({"element_id": segment["element_id"], "properties": database_risk_properties(risk, now)})
    updated = run_query(
        """
        UNWIND $rows AS row
        MATCH (segment:RouteSegment) WHERE elementId(segment)=row.element_id
        REMOVE segment.riskScore,segment.base_risk_score,segment.costRiskScore,
               segment.supplier_risk,segment.comprehensive_risk_score
        SET segment += row.properties,segment.risk_recalculated_at=datetime(row.properties.risk_recalculated_at)
        RETURN count(segment) AS updated
        """,
        {"rows": rows},
    )
    return int(updated[0]["updated"] if updated else 0)


def write_weather(port: dict[str, Any], weather: dict[str, Any], dry_run: bool = False) -> int:
    if dry_run: return 0
    settings = get_settings(); options = {"database": settings.database} if settings.database else {}
    query = """
    MATCH (p:Port) WHERE elementId(p)=$element_id
    SET p.weather_risk_score=$risk_score, p.weather_risk_level=$risk_level,
        p.weather_risk_confidence=$confidence, p.weather_data_completeness=$completeness,
        p.weather_risk_provider='Open-Meteo',p.weather_source='Open-Meteo Forecast API',
        p.weather_risk_trend=$trend, p.weather_risk_summary=$summary,
        p.weather_updated_at=datetime($fetched_at),p.weather_expires_at=datetime($expires_at),
        p.current_temperature_c=$temperature,
        p.current_relative_humidity=$humidity, p.current_precipitation_mm=$precipitation,
        p.current_visibility_m=$visibility, p.current_wind_speed_kmh=$wind_speed,
        p.current_wind_gusts_kmh=$wind_gusts, p.current_wind_direction_deg=$wind_direction,
        p.current_wave_height_m=$wave_height, p.current_wave_period_s=$wave_period,
        p.current_weather_code=$weather_code
    MERGE (w:WeatherRiskSnapshot {snapshot_id:$snapshot_id})
    ON CREATE SET w.observed_at=datetime($observed_at), w.fetched_at=datetime($fetched_at),
      w.expires_at=datetime($expires_at),
      w.current_risk_score=$risk_score,w.current_risk_level=$risk_level,w.max_risk_6h=$max6,
      w.max_risk_24h=$max24,w.average_risk_24h=$avg24,w.trend=$trend,w.confidence=$confidence,
      w.data_completeness=$completeness,w.temperature_c=$temperature,w.relative_humidity=$humidity,
      w.precipitation_mm=$precipitation,w.visibility_m=$visibility,w.wind_speed_kmh=$wind_speed,
      w.wind_gusts_kmh=$wind_gusts,w.wind_direction_deg=$wind_direction,w.wave_height_m=$wave_height,
      w.wave_period_s=$wave_period,w.weather_code=$weather_code,w.weather_source='Open-Meteo Forecast API',
      w.marine_source=$marine_source,w.scoring_version=$scoring_version,w.risk_factors_json=$factors_json
    SET w:RiskObservation,w.expires_at=datetime($expires_at),
      w.observation_id=$snapshot_id,w.observation_type='weather_risk',
      w.provider='Open-Meteo',w.source_type='open_api',w.data_status='observed',w.status='available',
      w.schema_version=$schema_version
    MERGE (p)-[:HAS_WEATHER_SNAPSHOT]->(w)
    MERGE (p)-[canonical:HAS_RISK_OBSERVATION]->(w)
    SET canonical.schema_version=$schema_version
    WITH p
    OPTIONAL MATCH (segment:RouteSegment)-[:FROM_NODE]->(p)
    WHERE coalesce(segment.mode,segment.routeMode)='sea'
    SET segment.origin_port_weather_risk=$risk_score,
        segment.origin_port_weather_confidence=$confidence,
        segment.origin_port_weather_snapshot_id=$snapshot_id,
        segment.route_weather_updated_at=datetime($fetched_at),
        segment.route_weather_expires_at=datetime($expires_at)
    WITH p
    OPTIONAL MATCH (segment:RouteSegment)-[:TO_NODE]->(p)
    WHERE coalesce(segment.mode,segment.routeMode)='sea'
    SET segment.destination_port_weather_risk=$risk_score,
        segment.destination_port_weather_confidence=$confidence,
        segment.destination_port_weather_snapshot_id=$snapshot_id,
        segment.route_weather_updated_at=datetime($fetched_at),
        segment.route_weather_expires_at=datetime($expires_at)
    WITH p
    MATCH (segment:RouteSegment) WHERE segment.origin_port_weather_risk IS NOT NULL OR segment.destination_port_weather_risk IS NOT NULL
    SET segment.route_weather_risk=CASE
      WHEN segment.origin_port_weather_risk IS NULL THEN segment.destination_port_weather_risk
      WHEN segment.destination_port_weather_risk IS NULL THEN segment.origin_port_weather_risk
      ELSE segment.origin_port_weather_risk*0.4+segment.destination_port_weather_risk*0.6 END,
      segment.route_weather_confidence=0.35 * CASE
        WHEN segment.origin_port_weather_confidence IS NULL THEN segment.destination_port_weather_confidence
        WHEN segment.destination_port_weather_confidence IS NULL THEN segment.origin_port_weather_confidence
        ELSE CASE WHEN segment.origin_port_weather_confidence < segment.destination_port_weather_confidence
                  THEN segment.origin_port_weather_confidence ELSE segment.destination_port_weather_confidence END END,
      segment.route_weather_provider='Open-Meteo',
      segment.route_weather_status='partial',
      segment.route_weather_sampling_method='endpoint_port_fallback',
      segment.route_weather_evidence=[item IN [segment.origin_port_weather_snapshot_id,segment.destination_port_weather_snapshot_id] WHERE item IS NOT NULL]
    RETURN count(DISTINCT segment) AS updated,collect(DISTINCT elementId(segment)) AS segment_ids
    """
    with get_driver().session(**options) as session:
        record = session.run(query, element_id=port["element_id"], schema_version=SCHEMA_VERSION, **weather).single()
    recalculate_segment_risk(list(record["segment_ids"] if record else []))
    return int(record["updated"] if record else 0)


def write_route_weather(rows: list[dict[str, Any]], dry_run: bool = False) -> int:
    if dry_run or not rows:
        return 0
    result = run_query(
        """
        UNWIND $rows AS row
        MATCH (segment:RouteSegment) WHERE elementId(segment)=row.element_id
        SET segment.route_weather_risk=row.score,
            segment.route_weather_level=row.level,
            segment.route_weather_status=row.status,
            segment.route_weather_confidence=row.confidence,
            segment.route_weather_data_completeness=row.data_completeness,
            segment.route_weather_provider='Open-Meteo',
            segment.route_weather_source=row.source,
            segment.route_weather_updated_at=datetime(row.fetched_at),
            segment.route_weather_expires_at=datetime(row.expires_at),
            segment.route_weather_sampling_method=row.sampling_method,
            segment.route_weather_geometry_status=row.geometry_status,
            segment.route_weather_sample_count=row.sample_count,
            segment.route_weather_valid_sample_count=row.valid_sample_count,
            segment.route_weather_forecast_hours=row.forecast_hours,
            segment.route_weather_marine_status=row.marine_status,
            segment.route_weather_evidence=[row.snapshot_id],
            segment.route_weather_scoring_version=$scoring_version
        MERGE (snapshot:RouteWeatherRiskSnapshot {snapshot_id:row.snapshot_id})
        SET snapshot:RiskObservation,snapshot.segment_id=row.segment_id,
            snapshot.mode=row.mode,snapshot.observed_at=datetime(row.fetched_at),
            snapshot.fetched_at=datetime(row.fetched_at),snapshot.expires_at=datetime(row.expires_at),
            snapshot.forecast_start_at=CASE WHEN row.forecast_start_at IS NULL THEN null ELSE datetime(row.forecast_start_at) END,
            snapshot.forecast_end_at=CASE WHEN row.forecast_end_at IS NULL THEN null ELSE datetime(row.forecast_end_at) END,
            snapshot.risk_score=row.score,snapshot.risk_level=row.level,snapshot.status=row.status,
            snapshot.confidence=row.confidence,snapshot.data_completeness=row.data_completeness,
            snapshot.sampling_method=row.sampling_method,snapshot.geometry_status=row.geometry_status,
            snapshot.marine_status=row.marine_status,
            snapshot.sample_count=row.sample_count,snapshot.valid_sample_count=row.valid_sample_count,
            snapshot.maximum_sample_risk=row.maximum_sample_risk,
            snapshot.average_sample_risk=row.average_sample_risk,
            snapshot.samples_json=row.samples_json,snapshot.risk_factors_json=row.factors_json,
            snapshot.provider='Open-Meteo',snapshot.source=row.source,
            snapshot.source_type='open_api',snapshot.data_status='observed',snapshot.is_inferred=true,
            snapshot.calculation_status='derived_from_observed_forecast',
            snapshot.observation_id=row.snapshot_id,
            snapshot.observation_type='route_weather_forecast_risk',
            snapshot.scoring_version=$scoring_version,snapshot.schema_version=$schema_version
        MERGE (segment)-[:HAS_ROUTE_WEATHER_SNAPSHOT]->(snapshot)
        MERGE (segment)-[canonical:HAS_RISK_OBSERVATION]->(snapshot)
        SET canonical.schema_version=$schema_version
        RETURN count(DISTINCT segment) AS updated,collect(DISTINCT elementId(segment)) AS segment_ids
        """,
        {
            "rows": rows,
            "scoring_version": rows[0]["scoring_version"],
            "schema_version": SCHEMA_VERSION,
        },
    )
    segment_ids = list(result[0]["segment_ids"] if result else [])
    recalculate_segment_risk(segment_ids)
    return int(result[0]["updated"] if result else 0)


def cleanup_snapshots(retention_days: int) -> int:
    rows=run_query("MATCH (w:WeatherRiskSnapshot) WHERE w.observed_at < datetime()-duration({days:$days}) WITH w LIMIT 10000 DETACH DELETE w RETURN count(*) AS deleted", {"days":retention_days})
    return rows[0]["deleted"] if rows else 0
