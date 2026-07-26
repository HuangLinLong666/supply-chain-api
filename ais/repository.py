from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ais.aggregation import PortTrafficSnapshot
from ais.config import AisSettings, AisTarget
from ais.models import NormalizedAisObservation
from app.provider_risk import parse_datetime
from database.neo4j_client import run_query
from database.unified_schema import SCHEMA_VERSION


AIS_SCHEMA_VERSION = "ais-port-traffic-v1"


class AisRepository:
    def ensure_schema(self) -> None:
        statements = (
            "CREATE CONSTRAINT unified_ais_target_id_unique IF NOT EXISTS FOR (n:AisObservationTarget) REQUIRE n.target_id IS UNIQUE",
            "CREATE CONSTRAINT unified_ais_provider_state_id_unique IF NOT EXISTS FOR (n:AisProviderState) REQUIRE n.provider_id IS UNIQUE",
            "CREATE CONSTRAINT unified_vessel_observation_id_unique IF NOT EXISTS FOR (n:VesselObservation) REQUIRE n.observation_id IS UNIQUE",
            "CREATE CONSTRAINT unified_vessel_mmsi_unique IF NOT EXISTS FOR (n:Vessel) REQUIRE n.mmsi IS UNIQUE",
            "CREATE CONSTRAINT unified_port_traffic_snapshot_id_unique IF NOT EXISTS FOR (n:PortTrafficSnapshot) REQUIRE n.snapshot_id IS UNIQUE",
            "CREATE INDEX unified_vessel_observation_observed IF NOT EXISTS FOR (n:VesselObservation) ON (n.position_observed_at)",
            "CREATE INDEX unified_ais_target_type IF NOT EXISTS FOR (n:AisObservationTarget) ON (n.target_type)",
            "CREATE INDEX unified_vessel_last_observed IF NOT EXISTS FOR (n:Vessel) ON (n.last_ais_observed_at)",
            "CREATE INDEX unified_port_traffic_observed IF NOT EXISTS FOR (n:PortTrafficSnapshot) ON (n.observed_at)",
            "CREATE INDEX unified_port_traffic_expires IF NOT EXISTS FOR (n:PortTrafficSnapshot) ON (n.expires_at)",
        )
        for statement in statements:
            run_query(statement)

    def sync_targets(self, targets: list[AisTarget]) -> None:
        rows = [
            {
                "target_id": target.target_id,
                "name": target.name,
                "target_type": target.target_type,
                "port_ids": list(target.port_ids),
                "normalized_port_ids": [item.replace("-", "").upper() for item in target.port_ids],
                "zone_id": target.zone_id,
                "aggregation_bbox_json": json.dumps(target.aggregation_bbox.as_subscription_box()),
                "subscription_bbox_json": json.dumps(target.subscription_bbox.as_subscription_box()),
                "congestion_reference_vessel_count": target.congestion_reference_vessel_count,
                "slow_speed_knots": target.slow_speed_knots,
                "schema_version": AIS_SCHEMA_VERSION,
            }
            for target in targets
        ]
        run_query(
            """
            UNWIND $rows AS row
            MERGE (target:AisObservationTarget {target_id:row.target_id})
            SET target.name=row.name,target.target_type=row.target_type,target.port_ids=row.port_ids,
                target.zone_id=row.zone_id,target.aggregation_bbox_json=row.aggregation_bbox_json,
                target.subscription_bbox_json=row.subscription_bbox_json,
                target.congestion_reference_vessel_count=row.congestion_reference_vessel_count,
                target.slow_speed_knots=row.slow_speed_knots,target.provider='AISStream.io',
                target.source='project AIS observation configuration',target.source_type='user_created',
                target.data_status='estimated',target.is_inferred=true,
                target.calculation_status='project_monitoring_scope',
                target.schema_version=row.schema_version,target.updated_at=datetime()
            """,
            {"rows": rows},
        )
        run_query(
            """
            UNWIND $rows AS row
            MATCH (target:AisObservationTarget {target_id:row.target_id})
            OPTIONAL MATCH (target)-[old:REPRESENTS_PORT]->(:Port)
            DELETE old
            WITH target,row
            OPTIONAL MATCH (port:Port)
            WHERE replace(toUpper(toString(coalesce(port.location_id,port.unlocode,port.code,port['port_id'],''))),'-','')
                  IN row.normalized_port_ids
            FOREACH (_ IN CASE WHEN port IS NULL THEN [] ELSE [1] END |
                MERGE (target)-[:REPRESENTS_PORT]->(port)
            )
            """,
            {"rows": [row for row in rows if row["target_type"] == "port"]},
        )
        run_query(
            """
            UNWIND $rows AS row
            MATCH (target:AisObservationTarget {target_id:row.target_id})
            OPTIONAL MATCH (target)-[old:REPRESENTS_ZONE]->(:GeoZone)
            DELETE old
            WITH target,row
            OPTIONAL MATCH (zone:GeoZone {zone_id:row.zone_id})
            FOREACH (_ IN CASE WHEN zone IS NULL THEN [] ELSE [1] END |
                MERGE (target)-[:REPRESENTS_ZONE]->(zone)
            )
            """,
            {"rows": [row for row in rows if row["target_type"] == "corridor"]},
        )

    def upsert_latest_observations(self, observations: list[NormalizedAisObservation]) -> int:
        if not observations:
            return 0
        rows = [observation.storage_row() for observation in observations]
        result = run_query(
            """
            UNWIND $rows AS row
            MERGE (vessel:Vessel {mmsi:row.mmsi})
            ON CREATE SET vessel.created_at=datetime(row.received_at)
            WITH row,vessel,
                 row.position_observed_at IS NOT NULL AND
                 (vessel.last_ais_observed_at IS NULL OR datetime(row.position_observed_at)>=vessel.last_ais_observed_at)
                 AS update_position
            SET vessel.provider='AISStream.io',vessel.source='AISStream.io WebSocket',
                vessel.source_url='https://aisstream.io/documentation.html',vessel.source_type='ais_observed',
                vessel.data_status='observed',vessel.status='available',vessel.is_inferred=false,
                vessel.excluded_from_risk=false,vessel.schema_version=$schema_version,
                vessel.last_message_type=row.message_type,vessel.last_message_types=row.message_types,
                vessel.last_message_observed_at=datetime(row.observed_at),
                vessel.last_message_received_at=datetime(row.received_at),
                vessel.imo=coalesce(row.imo,vessel.imo),vessel.name=coalesce(row.vessel_name,vessel.name),
                vessel.vessel_name=coalesce(row.vessel_name,vessel.vessel_name),
                vessel.vessel_type=coalesce(row.vessel_type,vessel.vessel_type),
                vessel.call_sign=coalesce(row.call_sign,vessel.call_sign),
                vessel.destination=coalesce(row.destination,vessel.destination),
                vessel.draught_m=coalesce(row.draught_m,vessel.draught_m),
                vessel.last_static_observed_at=CASE WHEN row.static_observed_at IS NULL
                    THEN vessel.last_static_observed_at ELSE datetime(row.static_observed_at) END,
                vessel.latitude=CASE WHEN update_position THEN row.latitude ELSE vessel.latitude END,
                vessel.longitude=CASE WHEN update_position THEN row.longitude ELSE vessel.longitude END,
                vessel.speed_knots=CASE WHEN update_position THEN row.speed_knots ELSE vessel.speed_knots END,
                vessel.course_degrees=CASE WHEN update_position THEN row.course_degrees ELSE vessel.course_degrees END,
                vessel.heading_degrees=CASE WHEN update_position THEN row.heading_degrees ELSE vessel.heading_degrees END,
                vessel.navigational_status_code=CASE WHEN update_position THEN row.navigational_status_code ELSE vessel.navigational_status_code END,
                vessel.navigational_status=CASE WHEN update_position THEN row.navigational_status ELSE vessel.navigational_status END,
                vessel.last_ais_observed_at=CASE WHEN update_position THEN datetime(row.position_observed_at) ELSE vessel.last_ais_observed_at END,
                vessel.updated_at=datetime(row.received_at)
            MERGE (observation:VesselObservation:Evidence {observation_id:'ais-latest:' + row.mmsi})
            ON CREATE SET observation.created_at=datetime(row.received_at)
            SET observation.evidence_id='ais-latest:' + row.mmsi,observation.mmsi=row.mmsi,
                observation.provider='AISStream.io',observation.source='AISStream.io WebSocket',
                observation.source_url='https://aisstream.io/documentation.html',observation.source_type='ais_observed',
                observation.data_status='observed',observation.status='available',observation.is_inferred=false,
                observation.schema_version=$schema_version,observation.message_type=row.message_type,
                observation.message_types=row.message_types,observation.observed_at=datetime(row.observed_at),
                observation.received_at=datetime(row.received_at),observation.raw_payload_hash=row.dedupe_key,
                observation.raw_payload_retained=false,
                observation.imo=coalesce(row.imo,observation.imo),
                observation.vessel_name=coalesce(row.vessel_name,observation.vessel_name),
                observation.vessel_type=coalesce(row.vessel_type,observation.vessel_type),
                observation.call_sign=coalesce(row.call_sign,observation.call_sign),
                observation.destination=coalesce(row.destination,observation.destination),
                observation.draught_m=coalesce(row.draught_m,observation.draught_m),
                observation.latitude=CASE WHEN update_position THEN row.latitude ELSE observation.latitude END,
                observation.longitude=CASE WHEN update_position THEN row.longitude ELSE observation.longitude END,
                observation.speed_knots=CASE WHEN update_position THEN row.speed_knots ELSE observation.speed_knots END,
                observation.course_degrees=CASE WHEN update_position THEN row.course_degrees ELSE observation.course_degrees END,
                observation.heading_degrees=CASE WHEN update_position THEN row.heading_degrees ELSE observation.heading_degrees END,
                observation.navigational_status_code=CASE WHEN update_position THEN row.navigational_status_code ELSE observation.navigational_status_code END,
                observation.navigational_status=CASE WHEN update_position THEN row.navigational_status ELSE observation.navigational_status END,
                observation.position_observed_at=CASE WHEN update_position THEN datetime(row.position_observed_at) ELSE observation.position_observed_at END,
                observation.static_observed_at=CASE WHEN row.static_observed_at IS NULL
                    THEN observation.static_observed_at ELSE datetime(row.static_observed_at) END,
                observation.updated_at=datetime(row.received_at)
            MERGE (vessel)-[:HAS_LATEST_OBSERVATION]->(observation)
            MERGE (vessel)-[:HAS_OBSERVATION]->(observation)
            RETURN count(DISTINCT vessel) AS updated
            """,
            {"rows": rows, "schema_version": AIS_SCHEMA_VERSION},
        )
        target_rows = [row for row in rows if row["target_ids"]]
        if target_rows:
            run_query(
                """
                UNWIND $rows AS row
                MATCH (observation:VesselObservation {observation_id:'ais-latest:' + row.mmsi})
                OPTIONAL MATCH (observation)-[old:OBSERVED_IN_AIS_TARGET]->(:AisObservationTarget)
                DELETE old
                WITH observation,row
                UNWIND row.target_ids AS target_id
                MATCH (target:AisObservationTarget {target_id:target_id})
                MERGE (observation)-[:OBSERVED_IN_AIS_TARGET]->(target)
                """,
                {"rows": target_rows},
            )
        return int(result[0]["updated"] if result else 0)

    def write_snapshots(self, snapshots: list[PortTrafficSnapshot]) -> dict[str, Any]:
        if not snapshots:
            expired = self.refresh_expired_segment_congestion()
            return {"snapshots": 0, "segments": expired}
        rows = [snapshot.storage_row() for snapshot in snapshots]
        result = run_query(
            """
            UNWIND $rows AS row
            MATCH (target:AisObservationTarget {target_id:row.target_id})
            MERGE (snapshot:PortTrafficSnapshot {snapshot_id:row.snapshot_id})
            SET snapshot:TrafficSnapshot:RiskObservation:Evidence,snapshot += row,
                snapshot.window_start=datetime(row.window_start),snapshot.window_end=datetime(row.window_end),
                snapshot.observed_at=datetime(row.observed_at),snapshot.generated_at=datetime(row.generated_at),
                snapshot.expires_at=datetime(row.expires_at),snapshot.updated_at=datetime()
            MERGE (target)-[:HAS_TRAFFIC_SNAPSHOT]->(snapshot)
            RETURN count(snapshot) AS updated
            """,
            {"rows": rows},
        )
        run_query(
            """
            UNWIND $rows AS row
            MATCH (target:AisObservationTarget {target_id:row.target_id})-[:HAS_TRAFFIC_SNAPSHOT]->
                  (snapshot:PortTrafficSnapshot {snapshot_id:row.snapshot_id})
            OPTIONAL MATCH (target)-[:REPRESENTS_PORT]->(port:Port)
            FOREACH (_ IN CASE WHEN port IS NULL THEN [] ELSE [1] END |
                MERGE (port)-[:HAS_TRAFFIC_SNAPSHOT]->(snapshot)
            )
            FOREACH (_ IN CASE WHEN port IS NOT NULL AND
                (port.traffic_observed_at IS NULL OR datetime(row.observed_at)>=port.traffic_observed_at)
                THEN [1] ELSE [] END |
                SET port.congestion_score=row.congestion_score_normalized,
                    port.congestion_score_100=row.congestion_score,
                    port.congestionRisk=row.congestion_score_normalized,
                    port.congestion_provider='AISStream.io',port.congestion_source='AISStream.io WebSocket aggregate',
                    port.traffic_provider='AISStream.io',port.traffic_status='available',
                    port.traffic_data_status='estimated',port.traffic_calculation_status='derived_from_observed_ais',
                    port.traffic_snapshot_id=row.snapshot_id,port.traffic_vessel_count=row.vessel_count,
                    port.traffic_anchored_count=row.anchored_count,port.traffic_average_speed_knots=row.average_speed,
                    port.traffic_arrival_count=row.arrival_count,port.traffic_departure_count=row.departure_count,
                    port.traffic_confidence=row.confidence,port.traffic_data_completeness=row.data_completeness,
                    port.traffic_observed_at=datetime(row.observed_at),port.traffic_expires_at=datetime(row.expires_at)
            )
            OPTIONAL MATCH (target)-[:REPRESENTS_ZONE]->(zone:GeoZone)
            FOREACH (_ IN CASE WHEN zone IS NULL THEN [] ELSE [1] END |
                MERGE (zone)-[:HAS_TRAFFIC_SNAPSHOT]->(snapshot)
            )
            """,
            {"rows": rows},
        )
        port_segments = run_query(
            """
            UNWIND $rows AS row
            MATCH (target:AisObservationTarget {target_id:row.target_id})-[:REPRESENTS_PORT]->(port:Port)
            MATCH (target)-[:HAS_TRAFFIC_SNAPSHOT]->(snapshot:PortTrafficSnapshot {snapshot_id:row.snapshot_id})
            MATCH (segment:RouteSegment)-[:FROM_NODE|TO_NODE]->(port)
            WHERE toLower(toString(coalesce(segment.canonical_mode,segment.mode,segment.routeMode,'')))='sea'
              AND coalesce(segment.feasibility_status,'') <> 'invalid_cross_ocean'
              AND coalesce(segment.data_status,'') <> 'synthetic'
              AND datetime(row.expires_at)>datetime()
            MERGE (segment)-[exposure:EXPOSED_TO_AIS_TRAFFIC]->(snapshot)
            SET exposure.target_id=row.target_id,exposure.exposure_method='route_endpoint_port',
                exposure.provider='AISStream.io',exposure.active=true,exposure.updated_at=datetime()
            RETURN collect(DISTINCT elementId(segment)) AS segment_ids
            """,
            {"rows": rows},
        )
        zone_segments = run_query(
            """
            UNWIND $rows AS row
            MATCH (target:AisObservationTarget {target_id:row.target_id})-[:REPRESENTS_ZONE]->(zone:GeoZone)
            MATCH (target)-[:HAS_TRAFFIC_SNAPSHOT]->(snapshot:PortTrafficSnapshot {snapshot_id:row.snapshot_id})
            MATCH (segment:RouteSegment)-[spatial:PASSES_THROUGH]->(zone)
            WHERE toLower(toString(coalesce(segment.canonical_mode,segment.mode,segment.routeMode,'')))='sea'
              AND coalesce(spatial.active,true)=true
              AND coalesce(segment.feasibility_status,'') <> 'invalid_cross_ocean'
              AND coalesce(segment.data_status,'') <> 'synthetic'
              AND datetime(row.expires_at)>datetime()
            MERGE (segment)-[exposure:EXPOSED_TO_AIS_TRAFFIC]->(snapshot)
            SET exposure.target_id=row.target_id,exposure.exposure_method='route_zone_intersection',
                exposure.provider='AISStream.io',exposure.active=true,exposure.updated_at=datetime()
            RETURN collect(DISTINCT elementId(segment)) AS segment_ids
            """,
            {"rows": rows},
        )
        segment_ids = sorted(
            {
                segment_id
                for result_set in (port_segments, zone_segments)
                for row in result_set
                for segment_id in row.get("segment_ids") or []
            }
        )
        updated_segments = self._update_segment_congestion(segment_ids)
        expired_segments = self.refresh_expired_segment_congestion(exclude_element_ids=segment_ids)
        return {
            "snapshots": int(result[0]["updated"] if result else 0),
            "segments": updated_segments + expired_segments,
        }

    def _update_segment_congestion(self, segment_ids: list[str]) -> int:
        if not segment_ids:
            return 0
        rows = run_query(
            """
            UNWIND $segment_ids AS segment_id
            MATCH (segment:RouteSegment) WHERE elementId(segment)=segment_id
            OPTIONAL MATCH (segment)-[:EXPOSED_TO_AIS_TRAFFIC]->(snapshot:PortTrafficSnapshot)
            WHERE snapshot.provider='AISStream.io'
              AND snapshot.source_type='ais_observed'
              AND snapshot.calculation_status='derived_from_observed_ais'
              AND snapshot.expires_at>datetime()
            WITH segment,snapshot ORDER BY snapshot.target_id,snapshot.observed_at DESC
            WITH segment,snapshot.target_id AS target_id,head(collect(snapshot)) AS latest_for_target
            WITH segment,[item IN collect(latest_for_target) WHERE item IS NOT NULL] AS active_snapshots
            WITH segment,active_snapshots,
                 reduce(highest=head(active_snapshots),item IN tail(active_snapshots) |
                     CASE WHEN item.congestion_score>highest.congestion_score THEN item ELSE highest END
                 ) AS highest
            SET segment.ais_congestion_score=highest.congestion_score,
                segment.ais_congestion_score_normalized=CASE WHEN highest IS NULL THEN null ELSE highest.congestion_score/100.0 END,
                segment.ais_congestion_provider=CASE WHEN highest IS NULL THEN null ELSE 'AISStream.io' END,
                segment.ais_congestion_status=CASE WHEN highest IS NULL THEN 'unavailable' ELSE 'available' END,
                segment.ais_congestion_confidence=highest.confidence,
                segment.ais_congestion_data_completeness=highest.data_completeness,
                segment.ais_congestion_observed_at=highest.observed_at,
                segment.ais_congestion_expires_at=highest.expires_at,
                segment.ais_congestion_snapshot_ids=[item IN active_snapshots | item.snapshot_id],
                segment.ais_congestion_target_ids=[item IN active_snapshots | item.target_id],
                segment.ais_congestion_updated_at=datetime()
            RETURN collect(elementId(segment)) AS segment_ids
            """,
            {"segment_ids": segment_ids},
        )
        recalculation_ids = rows[0].get("segment_ids") or [] if rows else []
        if recalculation_ids:
            from weather.repository import recalculate_segment_risk

            recalculate_segment_risk(recalculation_ids)
        return len(recalculation_ids)

    def refresh_expired_segment_congestion(self, exclude_element_ids: list[str] | None = None) -> int:
        rows = run_query(
            """
            MATCH (segment:RouteSegment)
            WHERE segment.ais_congestion_status='available'
              AND (segment.ais_congestion_expires_at IS NULL OR segment.ais_congestion_expires_at<=datetime())
              AND NOT elementId(segment) IN $excluded
            SET segment.ais_congestion_score=null,segment.ais_congestion_score_normalized=null,
                segment.ais_congestion_provider=null,segment.ais_congestion_status='unavailable',
                segment.ais_congestion_confidence=null,segment.ais_congestion_data_completeness=0.0,
                segment.ais_congestion_observed_at=null,segment.ais_congestion_expires_at=null,
                segment.ais_congestion_snapshot_ids=[],segment.ais_congestion_target_ids=[],
                segment.ais_congestion_updated_at=datetime()
            RETURN collect(elementId(segment)) AS segment_ids
            """,
            {"excluded": exclude_element_ids or []},
        )
        segment_ids = rows[0].get("segment_ids") or [] if rows else []
        if segment_ids:
            from weather.repository import recalculate_segment_risk

            recalculate_segment_risk(segment_ids)
        return len(segment_ids)

    def update_provider_state(self, **properties: Any) -> None:
        safe_properties = {key: value for key, value in properties.items() if key != "api_key"}
        safe_properties.update(
            {
                "provider": "AISStream.io",
                "source_url": "https://aisstream.io/documentation.html",
                "schema_version": AIS_SCHEMA_VERSION,
            }
        )
        run_query(
            """
            MERGE (state:AisProviderState {provider_id:'aisstream'})
            SET state += $properties,state.updated_at=datetime()
            """,
            {"properties": safe_properties},
        )

    def provider_status(self, settings: AisSettings | None = None) -> dict[str, Any]:
        rows = run_query(
            """
            OPTIONAL MATCH (state:AisProviderState {provider_id:'aisstream'})
            RETURN properties(state) AS state
            """
        )
        state = (rows[0].get("state") if rows else None) or {}
        configured = bool(state.get("configured")) or bool(settings and settings.configured)
        connected = bool(state.get("connected"))
        last_message_at = parse_datetime(state.get("last_message_at"))
        stale_seconds = settings.provider_stale_seconds if settings else 300
        fresh = bool(
            last_message_at
            and 0 <= (datetime.now(timezone.utc) - last_message_at).total_seconds() <= stale_seconds
        )
        stored_status = str(state.get("status") or "not_started")
        if not configured:
            status = "unavailable"
            reason = "missing_api_key_or_worker_not_configured"
        elif connected and fresh:
            status = "healthy"
            reason = None
        elif stored_status in {"authentication_error", "provider_error"}:
            status = "unavailable"
            reason = stored_status
        else:
            status = "degraded"
            reason = "worker_not_connected_or_no_recent_message"
        return {
            "id": "aisstream",
            "provider": "AISStream.io",
            "status": status,
            "reason": reason,
            "configured": configured,
            "connected": connected,
            "lastConnectedAt": state.get("last_connected_at"),
            "lastMessageAt": state.get("last_message_at"),
            "lastSnapshotAt": state.get("last_snapshot_at"),
            "lastSuccessfulFlushAt": state.get("last_successful_flush_at"),
            "lastErrorAt": state.get("last_error_at"),
            "lastError": state.get("last_error"),
            "messagesReceived": int(state.get("messages_received") or 0),
            "messagesParsed": int(state.get("messages_parsed") or 0),
            "duplicatesIgnored": int(state.get("duplicates_ignored") or 0),
            "malformedMessages": int(state.get("malformed_messages") or 0),
            "reconnectCount": int(state.get("reconnect_count") or 0),
            "apiKeyExposed": False,
        }

    def provider_statuses(self, settings: AisSettings | None = None) -> list[dict[str, Any]]:
        ais_status = self.provider_status(settings)
        gdelt_rows = run_query(
            """
            MATCH (zone:NewsRiskZone)
            WHERE toLower(toString(coalesce(zone.provider,'')))='gdelt'
            RETURN count(zone) AS total,
                   count(CASE WHEN zone.status='available' AND zone.expires_at>datetime() THEN 1 END) AS active,
                   max(zone.updated_at) AS last_updated_at,max(zone.expires_at) AS latest_expires_at
            """
        )
        weather_rows = run_query(
            """
            MATCH (node)
            WHERE (node:Port AND
                   toLower(toString(coalesce(node.weather_risk_provider,''))) IN ['open-meteo','open meteo'])
               OR (node:RouteSegment AND
                   toLower(toString(coalesce(node.route_weather_provider,''))) IN ['open-meteo','open meteo'])
            WITH CASE WHEN node:Port THEN node.weather_updated_at ELSE node.route_weather_updated_at END AS observed_at,
                 CASE WHEN node:Port THEN node.weather_expires_at ELSE node.route_weather_expires_at END AS expires_at
            RETURN count(*) AS total,
                   count(CASE WHEN expires_at>datetime() THEN 1 END) AS active,
                   max(observed_at) AS last_updated_at,max(expires_at) AS latest_expires_at
            """
        )
        return [
            ais_status,
            self._aggregate_provider_status("gdelt", "GDELT", gdelt_rows[0] if gdelt_rows else {}),
            self._aggregate_provider_status("open-meteo", "Open-Meteo", weather_rows[0] if weather_rows else {}),
        ]

    def list_targets(self) -> list[dict[str, Any]]:
        rows = run_query(
            """
            MATCH (target:AisObservationTarget)
            OPTIONAL MATCH (target)-[:HAS_TRAFFIC_SNAPSHOT]->(snapshot:PortTrafficSnapshot)
            WHERE snapshot.provider='AISStream.io'
              AND snapshot.source_type='ais_observed'
              AND snapshot.calculation_status='derived_from_observed_ais'
            WITH target,snapshot ORDER BY snapshot.observed_at DESC
            WITH target,head(collect(snapshot)) AS latest
            RETURN target.target_id AS targetId,target.name AS name,target.target_type AS type,
                   target.port_ids AS portIds,target.zone_id AS zoneId,
                   target.aggregation_bbox_json AS aggregationBoundingBoxJson,
                   target.subscription_bbox_json AS subscriptionBoundingBoxJson,
                   properties(latest) AS latestSnapshot
            ORDER BY targetId
            """
        )
        for row in rows:
            for source, destination in (
                ("aggregationBoundingBoxJson", "aggregationBoundingBox"),
                ("subscriptionBoundingBoxJson", "subscriptionBoundingBox"),
            ):
                value = row.pop(source, None)
                row[destination] = json.loads(value) if isinstance(value, str) else value
            row["traffic"] = self._format_snapshot(row.pop("latestSnapshot", None))
        return rows

    def port_traffic(self, port_id: str) -> dict[str, Any] | None:
        rows = run_query(
            """
            MATCH (port:Port)
            WHERE replace(toUpper(toString(coalesce(port.location_id,port.unlocode,port.code,port['port_id'],''))),'-','')
                  =replace(toUpper($port_id),'-','')
            OPTIONAL MATCH (target:AisObservationTarget)-[:REPRESENTS_PORT]->(port)
            OPTIONAL MATCH (target)-[:HAS_TRAFFIC_SNAPSHOT]->(snapshot:PortTrafficSnapshot)
            WHERE snapshot.provider='AISStream.io'
              AND snapshot.source_type='ais_observed'
              AND snapshot.calculation_status='derived_from_observed_ais'
            WITH port,target,snapshot ORDER BY snapshot.observed_at DESC
            WITH port,target,head(collect(snapshot)) AS latest
            RETURN coalesce(port.location_id,port.unlocode,port.code,port['port_id']) AS portId,
                   coalesce(port.name_zh,port.name,port.name_en,port.city) AS name,
                   port.city AS city,port.country AS country,port.latitude AS latitude,port.longitude AS longitude,
                   target.target_id AS targetId,target.name AS targetName,properties(latest) AS snapshot
            LIMIT 1
            """,
            {"port_id": port_id},
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "port": {
                "id": row.get("portId"),
                "name": row.get("name"),
                "city": row.get("city"),
                "country": row.get("country"),
                "lat": row.get("latitude"),
                "lng": row.get("longitude"),
            },
            "targetId": row.get("targetId"),
            "targetName": row.get("targetName"),
            "traffic": self._format_snapshot(row.get("snapshot")),
        }

    def target_traffic(self, target_id: str) -> dict[str, Any] | None:
        rows = run_query(
            """
            MATCH (target:AisObservationTarget {target_id:$target_id})
            OPTIONAL MATCH (target)-[:HAS_TRAFFIC_SNAPSHOT]->(snapshot:PortTrafficSnapshot)
            WHERE snapshot.provider='AISStream.io'
              AND snapshot.source_type='ais_observed'
              AND snapshot.calculation_status='derived_from_observed_ais'
            WITH target,snapshot ORDER BY snapshot.observed_at DESC
            WITH target,head(collect(snapshot)) AS latest
            RETURN target.target_id AS targetId,target.name AS name,target.target_type AS type,
                   target.port_ids AS portIds,target.zone_id AS zoneId,properties(latest) AS snapshot
            """,
            {"target_id": target_id},
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "targetId": row.get("targetId"),
            "name": row.get("name"),
            "type": row.get("type"),
            "portIds": row.get("portIds") or [],
            "zoneId": row.get("zoneId"),
            "traffic": self._format_snapshot(row.get("snapshot")),
        }

    def vessel(self, mmsi: str) -> dict[str, Any] | None:
        rows = run_query(
            """
            MATCH (vessel:Vessel {mmsi:$mmsi})
            OPTIONAL MATCH (vessel)-[:HAS_LATEST_OBSERVATION]->(observation:VesselObservation)
            OPTIONAL MATCH (observation)-[:OBSERVED_IN_AIS_TARGET]->(target:AisObservationTarget)
            RETURN properties(vessel) AS vessel,properties(observation) AS observation,
                   collect(DISTINCT {id:target.target_id,name:target.name,type:target.target_type}) AS targets
            """,
            {"mmsi": mmsi},
        )
        if not rows:
            return None
        vessel = rows[0].get("vessel") or {}
        observation = rows[0].get("observation") or {}
        return {
            "mmsi": vessel.get("mmsi"),
            "imo": vessel.get("imo"),
            "name": vessel.get("vessel_name") or vessel.get("name"),
            "type": vessel.get("vessel_type"),
            "callSign": vessel.get("call_sign"),
            "position": {
                "lat": vessel.get("latitude"),
                "lng": vessel.get("longitude"),
                "speedKnots": vessel.get("speed_knots"),
                "courseDegrees": vessel.get("course_degrees"),
                "headingDegrees": vessel.get("heading_degrees"),
                "observedAt": vessel.get("last_ais_observed_at"),
            },
            "destination": vessel.get("destination"),
            "draughtM": vessel.get("draught_m"),
            "navigationalStatus": vessel.get("navigational_status"),
            "navigationalStatusCode": vessel.get("navigational_status_code"),
            "provider": vessel.get("provider"),
            "dataStatus": vessel.get("data_status") or "unavailable",
            "excludedFromRisk": bool(vessel.get("excluded_from_risk")),
            "latestObservationId": observation.get("observation_id"),
            "rawPayloadRetained": bool(observation.get("raw_payload_retained")),
            "targets": [target for target in rows[0].get("targets") or [] if target.get("id")],
        }

    def mark_legacy_demo_observations(self) -> dict[str, int]:
        rows = run_query(
            """
            MATCH (vessel:Vessel)
            WHERE toUpper(coalesce(vessel.name,vessel.vessel_name,'')) CONTAINS 'DEMO'
               OR toUpper(coalesce(vessel.call_sign,'')) CONTAINS 'D3MO'
               OR vessel.source_type='fabricated_for_testing'
            SET vessel.data_status='synthetic',vessel.status='excluded',vessel.excluded_from_risk=true,
                vessel.exclusion_reason='legacy_demo_observation',vessel.reviewed_at=datetime()
            WITH collect(vessel) AS vessels
            UNWIND vessels AS vessel
            OPTIONAL MATCH (vessel)-[:HAS_OBSERVATION|HAS_LATEST_OBSERVATION]->(observation:VesselObservation)
            SET observation.data_status='synthetic',observation.status='excluded',observation.excluded_from_risk=true,
                observation.exclusion_reason='legacy_demo_observation',observation.reviewed_at=datetime()
            RETURN count(DISTINCT vessel) AS vessels,count(DISTINCT observation) AS observations
            """
        )
        return rows[0] if rows else {"vessels": 0, "observations": 0}

    def migration_summary(self) -> dict[str, Any]:
        rows = run_query(
            """
            OPTIONAL MATCH (target:AisObservationTarget)
            WITH count(target) AS targets
            OPTIONAL MATCH (vessel:Vessel)
            WITH targets,count(vessel) AS vessels,
                 count(CASE WHEN vessel.data_status='observed' AND coalesce(vessel.excluded_from_risk,false)=false THEN 1 END) AS observed_vessels,
                 count(CASE WHEN vessel.data_status='synthetic' OR coalesce(vessel.excluded_from_risk,false)=true THEN 1 END) AS excluded_vessels
            OPTIONAL MATCH (snapshot:PortTrafficSnapshot)
            RETURN targets,vessels,observed_vessels,excluded_vessels,count(snapshot) AS snapshots,
                   count(CASE WHEN snapshot.expires_at>datetime() AND snapshot.provider='AISStream.io' THEN 1 END) AS active_snapshots
            """
        )
        return rows[0] if rows else {}

    @staticmethod
    def _format_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
        if not snapshot:
            return None
        expires_at = parse_datetime(snapshot.get("expires_at"))
        active = bool(expires_at and expires_at > datetime.now(timezone.utc))
        components = snapshot.get("score_components_json")
        if isinstance(components, str):
            try:
                components = json.loads(components)
            except json.JSONDecodeError:
                components = None
        return {
            "status": "available" if active else "stale",
            "active": active,
            "snapshotId": snapshot.get("snapshot_id"),
            "vesselCount": snapshot.get("vessel_count"),
            "anchoredCount": snapshot.get("anchored_count"),
            "averageSpeedKnots": snapshot.get("average_speed"),
            "arrivalCount": snapshot.get("arrival_count"),
            "departureCount": snapshot.get("departure_count"),
            "congestionScore": snapshot.get("congestion_score") if active else None,
            "scoreComponents": components if active else None,
            "observationCount": snapshot.get("observation_count"),
            "confidence": snapshot.get("confidence"),
            "dataCompleteness": snapshot.get("data_completeness"),
            "provider": snapshot.get("provider"),
            "dataStatus": snapshot.get("data_status"),
            "calculationStatus": snapshot.get("calculation_status"),
            "observedAt": snapshot.get("observed_at"),
            "generatedAt": snapshot.get("generated_at"),
            "expiresAt": snapshot.get("expires_at"),
        }

    @staticmethod
    def _aggregate_provider_status(provider_id: str, provider: str, values: dict[str, Any]) -> dict[str, Any]:
        total = int(values.get("total") or 0)
        active = int(values.get("active") or 0)
        status = "healthy" if active else "degraded" if total else "unavailable"
        return {
            "id": provider_id,
            "provider": provider,
            "status": status,
            "reason": None if active else "no_fresh_data" if total else "no_provider_data",
            "configured": True,
            "dataPoints": total,
            "activeDataPoints": active,
            "lastUpdatedAt": values.get("last_updated_at"),
            "latestExpiresAt": values.get("latest_expires_at"),
            "apiKeyExposed": False,
        }
