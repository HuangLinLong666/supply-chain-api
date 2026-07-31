from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from database.country_identity import canonical_country_fields
from database.location_identity import LOCATION_ID_VERSION, canonical_location_id, location_aliases
from database.neo4j_client import get_driver, get_settings, to_jsonable
from database.unified_schema import SCHEMA_VERSION, data_status_for_source_type, unified_schema_statements
from app.vehicle_network.core import json_text
from app.vehicle_network.models import AuditSourceRequest, LocationRecord, RouteRecord


LABEL_BY_KIND = {
    "port": "Port",
    "airport": "Airport",
    "factory": "Factory",
    "rail_terminal": "RailTerminal",
    "road_terminal": "RoadTerminal",
}


class VehicleNetworkRepository:
    """整车运输网络的 Neo4j 持久化层。"""

    def _execute_write(self, callback):
        settings = get_settings()
        options = {"database": settings.database} if settings.database else {}
        with get_driver().session(**options) as session:
            return session.execute_write(callback)

    def _execute_read(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        settings = get_settings()
        options = {"database": settings.database} if settings.database else {}
        with get_driver().session(**options) as session:
            return [to_jsonable(record.data()) for record in session.run(query, parameters or {})]

    def ensure_schema(self) -> None:
        constraints = [
            "CREATE CONSTRAINT transport_location_id IF NOT EXISTS FOR (n:TransportLocation) REQUIRE n.location_id IS UNIQUE",
            "CREATE CONSTRAINT vehicle_route_id IF NOT EXISTS FOR (n:VehicleRoute) REQUIRE n.route_id IS UNIQUE",
            "CREATE CONSTRAINT route_leg_id IF NOT EXISTS FOR (n:RouteLeg) REQUIRE n.leg_id IS UNIQUE",
            "CREATE CONSTRAINT transport_evidence_id IF NOT EXISTS FOR (n:Evidence) REQUIRE n.evidence_id IS UNIQUE",
            "CREATE CONSTRAINT route_risk_snapshot_id IF NOT EXISTS FOR (n:RiskSnapshot) REQUIRE n.snapshot_id IS UNIQUE",
            "CREATE CONSTRAINT route_cost_estimate_id IF NOT EXISTS FOR (n:CostEstimate) REQUIRE n.estimate_id IS UNIQUE",
            "CREATE CONSTRAINT ingestion_job_id IF NOT EXISTS FOR (n:IngestionJob) REQUIRE n.job_id IS UNIQUE",
            "CREATE CONSTRAINT audit_log_id IF NOT EXISTS FOR (n:AuditLog) REQUIRE n.audit_id IS UNIQUE",
            *unified_schema_statements(),
        ]
        for statement in constraints:
            self._execute_write(lambda transaction, query=statement: transaction.run(query).consume())

    def start_job(self, job_type: str, trace_id: str) -> str:
        job_id = f"job_{uuid4().hex}"
        self._execute_write(lambda transaction: transaction.run("""
            MERGE (job:IngestionJob {job_id:$job_id})
            SET job.job_type=$job_type,job.trace_id=$trace_id,job.status='running',job.started_at=datetime()
        """, job_id=job_id, job_type=job_type, trace_id=trace_id).consume())
        return job_id

    def finish_job(self, job_id: str, status: str, summary: dict[str, Any]) -> None:
        self._execute_write(lambda transaction: transaction.run("""
            MATCH (job:IngestionJob {job_id:$job_id})
            SET job.status=$status,job.finished_at=datetime(),job.summary_json=$summary
        """, job_id=job_id, status=status, summary=json_text(summary)).consume())

    def merge_locations(self, locations: list[LocationRecord], job_id: str) -> int:
        rows = []
        for location in locations:
            row = location.model_dump(mode="json")
            row["label"] = LABEL_BY_KIND[location.kind.value]
            country_fields = canonical_country_fields(row)
            if country_fields is None:
                raise ValueError(f"地点 {location.id!r} 缺少有效的 ISO 3166-1 alpha-2 国家代码")
            row.update(country_fields)
            source_aliases = row.pop("aliases", [])
            raw_id = str(row["id"])
            row["id"] = canonical_location_id(
                [row["label"]],
                {**row, "location_id": raw_id},
                element_id=f"ingest:{location.kind.value}:{raw_id}",
            )
            row["location_aliases"] = list(
                dict.fromkeys([*location_aliases({**row, "location_id": raw_id}), *source_aliases])
            )
            row["aliases_json"] = json_text(source_aliases)
            row["canonical_location_id"] = row["id"]
            row["location_id_version"] = LOCATION_ID_VERSION
            row["schema_version"] = SCHEMA_VERSION
            row["location_kind"] = location.kind.value
            row["data_status"] = data_status_for_source_type(
                str(location.source_type),
                review_status=location.review_status,
                is_inferred=location.is_inferred,
            )
            rows.append(row)

        def write(transaction):
            for row in rows:
                properties = {key: value for key, value in row.items() if key not in {"id", "label", "updated_at"}}
                candidates = list(transaction.run(f"""
                    MATCH (location:{row['label']})
                    WHERE location.location_id=$id
                       OR any(alias IN coalesce(location.location_aliases,[]) WHERE alias IN $aliases)
                       OR ($unlocode IS NOT NULL AND (location.unlocode=$unlocode OR location.code=$unlocode))
                       OR ($iata IS NOT NULL AND (location.iata=$iata OR location.iata_code=$iata OR location.code=$iata))
                       OR ($icao IS NOT NULL AND location.icao=$icao)
                    RETURN elementId(location) AS element_id,
                           CASE WHEN location.location_id=$id THEN 0 ELSE 1 END AS priority
                    ORDER BY priority LIMIT 2
                """, id=row["id"], aliases=row["location_aliases"], unlocode=row.get("unlocode"), iata=row.get("iata"), icao=row.get("icao")))
                if len(candidates) > 1:
                    raise RuntimeError(f"地点身份冲突: {row['id']} 同时匹配多个已有节点，请先人工合并重复地点")
                if candidates:
                    transaction.run(f"""
                        MATCH (location) WHERE elementId(location)=$element_id
                        SET location:TransportLocation:{row['label']},
                            location.location_id=coalesce(location.location_id,$id),
                            location.canonical_location_id=coalesce(location.canonical_location_id,$id),
                            location.location_aliases=reduce(aliases=coalesce(location.location_aliases,[]), alias IN $aliases |
                                CASE WHEN alias IN aliases THEN aliases ELSE aliases + alias END),
                            location.location_id_version=$location_id_version,
                            location.name_en=coalesce(location.name_en,$name_en),
                            location.name_zh=coalesce(location.name_zh,$name_zh),
                            location.country_code=$country_code,
                            location.country=$country,
                            location.country_name_en=$country_name_en,
                            location.country_name_zh=$country_name_zh,
                            location.country_aliases=$country_aliases,
                            location.country_naming_version=$country_naming_version,
                            location.unlocode=coalesce(location.unlocode,$unlocode),
                            location.iata=coalesce(location.iata,$iata),
                            location.icao=coalesce(location.icao,$icao),
                            location.latitude=CASE WHEN location.latitude IS NULL OR location.latitude=0 THEN $latitude ELSE location.latitude END,
                            location.longitude=CASE WHEN location.longitude IS NULL OR location.longitude=0 THEN $longitude ELSE location.longitude END,
                            location.eligible_for_vehicle_export=coalesce(location.eligible_for_vehicle_export,false) OR $eligible_export,
                            location.eligible_for_vehicle_import=coalesce(location.eligible_for_vehicle_import,false) OR $eligible_import,
                            location.vehicle_network_source=$source,
                            location.vehicle_network_source_type=$source_type,
                            location.vehicle_network_confidence=$confidence,
                            location.vehicle_network_updated_at=datetime($updated_at),
                            location.source=coalesce(location.source,$source),
                            location.source_type=coalesce(location.source_type,$source_type),
                            location.source_url=coalesce(location.source_url,$source_url),
                            location.collected_at=coalesce(location.collected_at,datetime($collected_at)),
                            location.confidence=coalesce(location.confidence,$confidence),
                            location.is_inferred=coalesce(location.is_inferred,$is_inferred),
                            location.data_status=$data_status,
                            location.location_kind=$location_kind,
                            location.schema_version=$schema_version,
                            location.deleted_at=null
                        WITH location
                        MATCH (job:IngestionJob {{job_id:$job_id}})
                        MERGE (job)-[:INGESTED]->(location)
                    """, element_id=candidates[0]["element_id"], id=row["id"], aliases=row["location_aliases"],
                         location_id_version=LOCATION_ID_VERSION, name_en=row.get("name_en"),
                         name_zh=row.get("name_zh"), country_code=row.get("country_code"), country=row.get("country"),
                         country_name_en=row.get("country_name_en"), country_name_zh=row.get("country_name_zh"),
                         country_aliases=row.get("country_aliases"), country_naming_version=row.get("country_naming_version"),
                         unlocode=row.get("unlocode"),
                         iata=row.get("iata"), icao=row.get("icao"), latitude=row.get("latitude"), longitude=row.get("longitude"),
                         eligible_export=row.get("eligible_for_vehicle_export", False), eligible_import=row.get("eligible_for_vehicle_import", False),
                         source=row.get("source"), source_type=row.get("source_type"), confidence=row.get("confidence"),
                         source_url=row.get("source_url"), collected_at=row.get("collected_at"), is_inferred=row.get("is_inferred"),
                         data_status=row["data_status"], location_kind=row["location_kind"], schema_version=SCHEMA_VERSION,
                         updated_at=row["updated_at"], job_id=job_id).consume()
                else:
                    transaction.run(f"""
                        MERGE (location:TransportLocation:{row['label']} {{location_id:$id}})
                        SET location += $properties,location.updated_at=datetime($updated_at),location.deleted_at=null
                        WITH location
                        MATCH (job:IngestionJob {{job_id:$job_id}})
                        MERGE (job)-[:INGESTED]->(location)
                    """, id=row["id"], properties=properties, updated_at=row["updated_at"], job_id=job_id).consume()
            return len(rows)

        return self._execute_write(write)

    def get_location(self, location_id: str) -> dict[str, Any] | None:
        expected = location_id.strip()
        rows = self._execute_read("""
            MATCH (location)
            WHERE location.deleted_at IS NULL AND (
                toLower(coalesce(location.location_id,''))=toLower($value)
                OR any(alias IN coalesce(location.location_aliases,[]) WHERE toLower(alias)=toLower($value))
                OR toLower(coalesce(location.unlocode,''))=toLower($value)
                OR toLower(coalesce(location.code,''))=toLower($value)
                OR toLower(coalesce(location.iata,''))=toLower($value)
                OR toLower(coalesce(location.iata_code,''))=toLower($value)
                OR toLower(coalesce(location.icao,''))=toLower($value)
                OR toLower(coalesce(location.name,''))=toLower($value)
                OR toLower(coalesce(location.name_en,''))=toLower($value)
                OR toLower(coalesce(location.name_zh,''))=toLower($value)
                OR toLower(coalesce(location.city,''))=toLower($value)
            )
            WITH location,
                 CASE
                   WHEN toLower(coalesce(location.location_id,''))=toLower($value) THEN 0
                   WHEN toLower(coalesce(location.unlocode,location.code,location.iata,location.iata_code,''))=toLower($value) THEN 1
                   WHEN toLower(coalesce(location.name,location.name_en,location.name_zh,''))=toLower($value) THEN 2
                   ELSE 3
                 END AS priority
            ORDER BY CASE WHEN location:TransportLocation THEN 0 ELSE 1 END,
                     CASE WHEN location:Port OR location:Airport OR location:Factory THEN 0 ELSE 1 END,
                     priority
            RETURN properties(location) AS location,labels(location) AS labels,elementId(location) AS element_id
            LIMIT 1
        """, {"value": expected})
        if not rows:
            rows = self._execute_read("""
                MATCH (location)
                WHERE location.deleted_at IS NULL AND (
                    toLower(coalesce(location.name,'')) CONTAINS toLower($value)
                    OR toLower(coalesce(location.name_en,'')) CONTAINS toLower($value)
                    OR toLower(coalesce(location.city,'')) CONTAINS toLower($value)
                )
                ORDER BY CASE WHEN location:Port OR location:Airport OR location:Factory THEN 0 ELSE 1 END
                RETURN properties(location) AS location,labels(location) AS labels,elementId(location) AS element_id
                LIMIT 1
            """, {"value": expected})
        return (rows[0]["location"] | {"labels": rows[0]["labels"], "element_id": rows[0]["element_id"]}) if rows else None

    def merge_route(self, route: RouteRecord, job_id: str | None = None) -> None:
        payload = route.model_dump(mode="json", exclude={"legs", "risk", "estimated_cost", "origin", "destination"})
        payload["schema_version"] = SCHEMA_VERSION
        payload["data_status"] = data_status_for_source_type(
            str(route.source_type), review_status=route.review_status, is_inferred=route.is_inferred
        )
        payload["scoring_version"] = "vehicle-network-v1"
        payload["validity_status"] = "unavailable"
        payload["why_recommended_json"] = json_text(payload.pop("why_recommended", []))
        if route.origin:
            payload["origin_name"] = route.origin.name
            payload["origin_name_zh"] = route.origin.name_zh
        if route.destination:
            payload["destination_name"] = route.destination.name
            payload["destination_name_zh"] = route.destination.name_zh

        def write(transaction):
            transaction.run("""
                MERGE (route:VehicleRoute:Route {route_id:$route_id})
                SET route += $properties,route.updated_at=datetime(),route.deleted_at=null
                WITH route
                MATCH (origin) WHERE origin.location_id=$origin_id OR $origin_id IN coalesce(origin.location_aliases,[])
                MATCH (destination) WHERE destination.location_id=$destination_id OR $destination_id IN coalesce(destination.location_aliases,[])
                MERGE (route)-[:ORIGIN]->(origin)
                MERGE (route)-[:DESTINATION]->(destination)
            """, route_id=route.route_id, properties=payload, origin_id=route.origin_id, destination_id=route.destination_id).consume()
            for leg in route.legs:
                properties = leg.model_dump(mode="json")
                properties["geometry_json"] = json_text(properties.pop("geometry", []))
                properties["evidence_refs_json"] = json_text(properties.pop("evidence_refs", []))
                from_location = properties.pop("from_location", None)
                to_location = properties.pop("to_location", None)
                properties["from_location_json"] = json_text(from_location or {})
                properties["to_location_json"] = json_text(to_location or {})
                properties["from_name"] = (from_location or {}).get("name")
                properties["to_name"] = (to_location or {}).get("name")
                properties["segment_id"] = leg.leg_id
                properties["schema_version"] = SCHEMA_VERSION
                properties["data_status"] = data_status_for_source_type(
                    str(leg.source_type), review_status=leg.review_status, is_inferred=leg.is_inferred
                )
                properties["canonical_mode"] = leg.mode if leg.mode in {"road", "rail", "sea", "air"} else None
                properties["scoring_version"] = "vehicle-network-v1"
                properties["feasibility_status"] = "estimated_requires_review"
                properties["validity_status"] = "unavailable"
                transaction.run("""
                    MATCH (route:VehicleRoute {route_id:$route_id})
                    MERGE (leg:RouteLeg {leg_id:$leg_id})
                    SET leg:RouteSegment,leg += $properties
                    MERGE (route)-[relationship:HAS_LEG]->(leg) SET relationship.sequence=$sequence
                    MERGE (route)-[canonical:HAS_SEGMENT]->(leg)
                    SET canonical.sequence=$sequence,canonical.schema_version=$schema_version
                    WITH leg
                    MATCH (origin) WHERE origin.location_id=$origin_id OR $origin_id IN coalesce(origin.location_aliases,[])
                    MATCH (destination) WHERE destination.location_id=$destination_id OR $destination_id IN coalesce(destination.location_aliases,[])
                    MERGE (leg)-[:FROM_NODE]->(origin)
                    MERGE (leg)-[:TO_NODE]->(destination)
                """, route_id=route.route_id, leg_id=leg.leg_id, properties=properties, sequence=leg.sequence,
                     schema_version=SCHEMA_VERSION,
                     origin_id=leg.origin_id, destination_id=leg.destination_id).consume()
            if route.risk:
                snapshot_id = f"risk_{route.route_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
                transaction.run("""
                    MATCH (route:VehicleRoute {route_id:$route_id})
                    MERGE (snapshot:RiskSnapshot {snapshot_id:$snapshot_id})
                    SET snapshot:RiskObservation,snapshot += $properties,snapshot.calculated_at=datetime(),
                        snapshot.observation_id=$snapshot_id,snapshot.observation_type='route_risk_snapshot',
                        snapshot.observed_at=datetime(),snapshot.data_status='estimated',snapshot.status='unavailable',
                        snapshot.schema_version=$schema_version
                    MERGE (route)-[:HAS_RISK_SNAPSHOT]->(snapshot)
                    MERGE (route)-[canonical:HAS_RISK_OBSERVATION]->(snapshot)
                    SET canonical.schema_version=$schema_version
                """, route_id=route.route_id, snapshot_id=snapshot_id, schema_version=SCHEMA_VERSION,
                     properties={**route.risk.model_dump(mode="json"), "risk_factors_json": json_text(route.risk.risk_factors), "evidence_refs_json": json_text(route.risk.evidence_refs)}).consume()
            if route.estimated_cost:
                estimate_id = f"cost_{route.route_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
                cost_properties = route.estimated_cost.model_dump(mode="json")
                cost_properties["input_snapshot_json"] = json_text(cost_properties.pop("input_snapshot"))
                transaction.run("""
                    MATCH (route:VehicleRoute {route_id:$route_id})
                    MERGE (estimate:CostEstimate {estimate_id:$estimate_id})
                    SET estimate:CostObservation,estimate += $properties,estimate.calculated_at=datetime(),
                        estimate.observation_id=$estimate_id,estimate.observation_type='route_cost_estimate',
                        estimate.observed_at=datetime(),estimate.data_status='estimated',estimate.status='unavailable',
                        estimate.schema_version=$schema_version
                    MERGE (route)-[:HAS_COST_ESTIMATE]->(estimate)
                    MERGE (route)-[canonical:HAS_COST_OBSERVATION]->(estimate)
                    SET canonical.schema_version=$schema_version
                """, route_id=route.route_id, estimate_id=estimate_id, schema_version=SCHEMA_VERSION,
                     properties=cost_properties).consume()
            if job_id:
                transaction.run("""MATCH (job:IngestionJob {job_id:$job_id}),(route:VehicleRoute {route_id:$route_id}) MERGE (job)-[:GENERATED]->(route)""", job_id=job_id, route_id=route.route_id).consume()

        self._execute_write(write)

    def search_routes(self, origin: str, destination: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._execute_read("""
            MATCH (route:VehicleRoute)-[:ORIGIN]->(origin)
            MATCH (route)-[:DESTINATION]->(destination)
            WHERE route.deleted_at IS NULL
              AND (toLower(coalesce(origin.location_id,''))=toLower($origin)
                   OR toLower(coalesce(origin.unlocode,''))=toLower($origin)
                   OR toLower(coalesce(origin.code,''))=toLower($origin)
                   OR toLower(coalesce(origin.iata,''))=toLower($origin)
                   OR toLower(coalesce(origin.iata_code,''))=toLower($origin)
                   OR toLower(coalesce(origin.name,''))=toLower($origin)
                   OR toLower(coalesce(origin.name_en,''))=toLower($origin)
                   OR toLower(coalesce(origin.name_zh,''))=toLower($origin)
                   OR toLower(coalesce(origin.city,''))=toLower($origin))
              AND (toLower(coalesce(destination.location_id,''))=toLower($destination)
                   OR toLower(coalesce(destination.unlocode,''))=toLower($destination)
                   OR toLower(coalesce(destination.code,''))=toLower($destination)
                   OR toLower(coalesce(destination.iata,''))=toLower($destination)
                   OR toLower(coalesce(destination.iata_code,''))=toLower($destination)
                   OR toLower(coalesce(destination.name,''))=toLower($destination)
                   OR toLower(coalesce(destination.name_en,''))=toLower($destination)
                   OR toLower(coalesce(destination.name_zh,''))=toLower($destination)
                   OR toLower(coalesce(destination.city,''))=toLower($destination))
            OPTIONAL MATCH (route)-[membership:HAS_LEG]->(leg:RouteLeg)
            OPTIONAL MATCH (route)-[:HAS_RISK_SNAPSHOT]->(risk:RiskSnapshot)
            OPTIONAL MATCH (route)-[:HAS_COST_ESTIMATE]->(cost:CostEstimate)
            WITH route,origin,destination,leg,membership,risk,cost ORDER BY membership.sequence,risk.calculated_at DESC,cost.calculated_at DESC
            RETURN properties(route) AS route,
                   {id:coalesce(origin.location_id,origin.unlocode,origin.code,origin.iata),
                    name:coalesce(origin.name_zh,origin.name,origin.name_en,origin.city),
                    nameZh:origin.name_zh,nameEn:coalesce(origin.name_en,origin.name),city:origin.city,
                    country:origin.country,countryCode:origin.country_code,latitude:origin.latitude,longitude:origin.longitude} AS origin,
                   {id:coalesce(destination.location_id,destination.unlocode,destination.code,destination.iata),
                    name:coalesce(destination.name_zh,destination.name,destination.name_en,destination.city),
                    nameZh:destination.name_zh,nameEn:coalesce(destination.name_en,destination.name),city:destination.city,
                    country:destination.country,countryCode:destination.country_code,latitude:destination.latitude,longitude:destination.longitude} AS destination,
                   collect(DISTINCT properties(leg)) AS legs,
                   head(collect(DISTINCT properties(risk))) AS risk,head(collect(DISTINCT properties(cost))) AS cost
            ORDER BY route.score DESC LIMIT $limit
        """, {"origin": origin, "destination": destination, "limit": limit})

    def get_route(self, route_id: str) -> dict[str, Any] | None:
        rows = self._execute_read("""
            MATCH (route:VehicleRoute {route_id:$route_id})
            OPTIONAL MATCH (route)-[:ORIGIN]->(origin)
            OPTIONAL MATCH (route)-[:DESTINATION]->(destination)
            OPTIONAL MATCH (route)-[membership:HAS_LEG]->(leg:RouteLeg)
            OPTIONAL MATCH (leg)-[:FROM_NODE]->(legOrigin)
            OPTIONAL MATCH (leg)-[:TO_NODE]->(legDestination)
            WITH route,origin,destination,leg,legOrigin,legDestination,membership ORDER BY membership.sequence
            RETURN properties(route) AS route,
                   {id:coalesce(origin.location_id,origin.unlocode,origin.code,origin.iata),name:coalesce(origin.name_zh,origin.name,origin.name_en,origin.city),nameZh:origin.name_zh,nameEn:coalesce(origin.name_en,origin.name),city:origin.city,country:origin.country,countryCode:origin.country_code,latitude:origin.latitude,longitude:origin.longitude} AS origin,
                   {id:coalesce(destination.location_id,destination.unlocode,destination.code,destination.iata),name:coalesce(destination.name_zh,destination.name,destination.name_en,destination.city),nameZh:destination.name_zh,nameEn:coalesce(destination.name_en,destination.name),city:destination.city,country:destination.country,countryCode:destination.country_code,latitude:destination.latitude,longitude:destination.longitude} AS destination,
                   collect(leg{.*,
                     from_location:{id:coalesce(legOrigin.location_id,legOrigin.unlocode,legOrigin.code,legOrigin.iata),name:coalesce(legOrigin.name_zh,legOrigin.name,legOrigin.name_en,legOrigin.city),city:legOrigin.city,country:legOrigin.country,latitude:legOrigin.latitude,longitude:legOrigin.longitude},
                     to_location:{id:coalesce(legDestination.location_id,legDestination.unlocode,legDestination.code,legDestination.iata),name:coalesce(legDestination.name_zh,legDestination.name,legDestination.name_en,legDestination.city),city:legDestination.city,country:legDestination.country,latitude:legDestination.latitude,longitude:legDestination.longitude}
                   }) AS legs
        """, {"route_id": route_id})
        return rows[0] if rows else None

    def review_route(self, route_id: str, status: str, reviewer: str, note: str | None) -> bool:
        rows = self._execute_read("""
            MATCH (route:VehicleRoute {route_id:$route_id})
            SET route.review_status=$status,route.reviewed_by=$reviewer,route.reviewed_at=datetime(),route.review_note=$note
            CREATE (audit:AuditLog {audit_id:$audit_id,action:'review',entity_id:$route_id,actor:$reviewer,note:$note,created_at:datetime()})
            MERGE (audit)-[:AUDITS]->(route)
            RETURN route.route_id AS route_id
        """, {"route_id": route_id, "status": status, "reviewer": reviewer, "note": note, "audit_id": f"audit_{uuid4().hex}"})
        return bool(rows)

    def soft_delete_route(self, route_id: str, actor: str = "api_user") -> bool:
        rows = self._execute_read("""
            MATCH (route:VehicleRoute {route_id:$route_id})
            SET route.deleted_at=datetime(),route.route_status='deleted'
            CREATE (audit:AuditLog {audit_id:$audit_id,action:'soft_delete',entity_id:$route_id,actor:$actor,created_at:datetime()})
            MERGE (audit)-[:AUDITS]->(route)
            RETURN route.route_id AS route_id
        """, {"route_id": route_id, "actor": actor, "audit_id": f"audit_{uuid4().hex}"})
        return bool(rows)

    def add_source_audit(self, request: AuditSourceRequest) -> str:
        evidence_id = f"evidence_{uuid4().hex}"
        self._execute_write(lambda transaction: transaction.run("""
            MERGE (evidence:Evidence {evidence_id:$evidence_id})
            SET evidence += $properties,evidence.collected_at=datetime(),
                evidence.schema_version=$schema_version,evidence.data_status=$data_status
            WITH evidence
            OPTIONAL MATCH (entity) WHERE coalesce(entity.route_id,entity.leg_id,entity.location_id,entity.evidence_id)=$entity_id
            FOREACH (_ IN CASE WHEN entity IS NULL THEN [] ELSE [1] END | MERGE (entity)-[:SUPPORTED_BY]->(evidence))
        """, evidence_id=evidence_id, entity_id=request.entity_id, schema_version=SCHEMA_VERSION,
             data_status=data_status_for_source_type(str(request.source_type)),
             properties=request.model_dump(mode="json", exclude={"entity_id", "entity_type"})).consume())
        return evidence_id
