"""Neo4j persistence for port weather state and snapshots."""

from __future__ import annotations

from typing import Any

from database.neo4j_client import get_driver, get_settings, run_query


def list_ports(port_ids: list[str] | None = None) -> list[dict[str, Any]]:
    return run_query("""
    MATCH (p:Port)
    WITH p, coalesce(p.unlocode,p.code,p['port_id'],elementId(p)) AS port_id
    WHERE $port_ids IS NULL OR port_id IN $port_ids
    RETURN elementId(p) AS element_id, port_id, p.name AS name, p.city AS city, p.country AS country,
           p.iso2 AS country_code, p.latitude AS latitude, p.longitude AS longitude,
           p.weather_updated_at AS weather_updated_at
    ORDER BY name
    """, {"port_ids": port_ids})


def ensure_schema() -> None:
    statements = [
        "CREATE CONSTRAINT weather_snapshot_id IF NOT EXISTS FOR (w:WeatherRiskSnapshot) REQUIRE w.snapshot_id IS UNIQUE",
        "CREATE INDEX port_weather_risk_level IF NOT EXISTS FOR (p:Port) ON (p.weather_risk_level)",
        "CREATE INDEX port_weather_updated_at IF NOT EXISTS FOR (p:Port) ON (p.weather_updated_at)",
    ]
    settings = get_settings(); options = {"database": settings.database} if settings.database else {}
    with get_driver().session(**options) as session:
        for statement in statements: session.run(statement).consume()


def write_weather(port: dict[str, Any], weather: dict[str, Any], dry_run: bool = False) -> int:
    if dry_run: return 0
    settings = get_settings(); options = {"database": settings.database} if settings.database else {}
    query = """
    MATCH (p:Port) WHERE elementId(p)=$element_id
    SET p.weather_risk_score=$risk_score, p.weather_risk_level=$risk_level,
        p.weather_risk_confidence=$confidence, p.weather_data_completeness=$completeness,
        p.weather_risk_trend=$trend, p.weather_risk_summary=$summary,
        p.weather_updated_at=datetime($fetched_at), p.current_temperature_c=$temperature,
        p.current_relative_humidity=$humidity, p.current_precipitation_mm=$precipitation,
        p.current_visibility_m=$visibility, p.current_wind_speed_kmh=$wind_speed,
        p.current_wind_gusts_kmh=$wind_gusts, p.current_wind_direction_deg=$wind_direction,
        p.current_wave_height_m=$wave_height, p.current_wave_period_s=$wave_period,
        p.current_weather_code=$weather_code
    MERGE (w:WeatherRiskSnapshot {snapshot_id:$snapshot_id})
    ON CREATE SET w.observed_at=datetime($observed_at), w.fetched_at=datetime($fetched_at),
      w.current_risk_score=$risk_score,w.current_risk_level=$risk_level,w.max_risk_6h=$max6,
      w.max_risk_24h=$max24,w.average_risk_24h=$avg24,w.trend=$trend,w.confidence=$confidence,
      w.data_completeness=$completeness,w.temperature_c=$temperature,w.relative_humidity=$humidity,
      w.precipitation_mm=$precipitation,w.visibility_m=$visibility,w.wind_speed_kmh=$wind_speed,
      w.wind_gusts_kmh=$wind_gusts,w.wind_direction_deg=$wind_direction,w.wave_height_m=$wave_height,
      w.wave_period_s=$wave_period,w.weather_code=$weather_code,w.weather_source='Open-Meteo Forecast API',
      w.marine_source=$marine_source,w.scoring_version=$scoring_version,w.risk_factors_json=$factors_json
    MERGE (p)-[:HAS_WEATHER_SNAPSHOT]->(w)
    WITH p
    OPTIONAL MATCH (segment:RouteSegment)-[:FROM_NODE]->(p)
    WHERE coalesce(segment.mode,segment.routeMode)='sea'
    SET segment.origin_port_weather_risk=$risk_score,segment.route_weather_updated_at=datetime($fetched_at)
    WITH p
    OPTIONAL MATCH (segment:RouteSegment)-[:TO_NODE]->(p)
    WHERE coalesce(segment.mode,segment.routeMode)='sea'
    SET segment.destination_port_weather_risk=$risk_score,segment.route_weather_updated_at=datetime($fetched_at)
    WITH p
    MATCH (segment:RouteSegment) WHERE segment.origin_port_weather_risk IS NOT NULL OR segment.destination_port_weather_risk IS NOT NULL
    SET segment.route_weather_risk=CASE
      WHEN segment.origin_port_weather_risk IS NULL THEN segment.destination_port_weather_risk
      WHEN segment.destination_port_weather_risk IS NULL THEN segment.origin_port_weather_risk
      ELSE segment.origin_port_weather_risk*0.4+segment.destination_port_weather_risk*0.6 END
    RETURN count(DISTINCT segment) AS updated
    """
    with get_driver().session(**options) as session:
        record = session.run(query, element_id=port["element_id"], **weather).single()
        return int(record["updated"] if record else 0)


def cleanup_snapshots(retention_days: int) -> int:
    rows=run_query("MATCH (w:WeatherRiskSnapshot) WHERE w.observed_at < datetime()-duration({days:$days}) WITH w LIMIT 10000 DETACH DELETE w RETURN count(*) AS deleted", {"days":retention_days})
    return rows[0]["deleted"] if rows else 0
