from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from database.neo4j_client import get_driver, get_settings, to_jsonable
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
            row["aliases_json"] = json_text(row.pop("aliases", []))
            rows.append(row)

        def write(transaction):
            for row in rows:
                query = f"""
                    MERGE (location:TransportLocation:{row['label']} {{location_id:$id}})
                    SET location += $properties,location.updated_at=datetime($updated_at),location.deleted_at=null
                    WITH location
                    MATCH (job:IngestionJob {{job_id:$job_id}})
                    MERGE (job)-[:INGESTED]->(location)
                """
                properties = {key: value for key, value in row.items() if key not in {"id", "label", "updated_at"}}
                transaction.run(query, id=row["id"], properties=properties, updated_at=row["updated_at"], job_id=job_id).consume()
            return len(rows)

        return self._execute_write(write)

    def get_location(self, location_id: str) -> dict[str, Any] | None:
        rows = self._execute_read("""
            MATCH (location:TransportLocation {location_id:$location_id})
            WHERE location.deleted_at IS NULL
            RETURN properties(location) AS location,labels(location) AS labels
        """, {"location_id": location_id})
        if rows:
            return rows[0]["location"] | {"labels": rows[0]["labels"]}
        rows = self._execute_read("""
            MATCH (location) WHERE coalesce(location.unlocode,location.code,location.id,location.location_id)=$location_id
            RETURN properties(location) AS location,labels(location) AS labels LIMIT 1
        """, {"location_id": location_id})
        return (rows[0]["location"] | {"labels": rows[0]["labels"]}) if rows else None

    def merge_route(self, route: RouteRecord, job_id: str | None = None) -> None:
        payload = route.model_dump(mode="json", exclude={"legs", "risk", "estimated_cost"})
        payload["why_recommended_json"] = json_text(payload.pop("why_recommended", []))

        def write(transaction):
            transaction.run("""
                MERGE (route:VehicleRoute:Route {route_id:$route_id})
                SET route += $properties,route.updated_at=datetime(),route.deleted_at=null
                WITH route
                MATCH (origin) WHERE coalesce(origin.location_id,origin.unlocode,origin.code,origin.id)=$origin_id
                MATCH (destination) WHERE coalesce(destination.location_id,destination.unlocode,destination.code,destination.id)=$destination_id
                MERGE (route)-[:ORIGIN]->(origin)
                MERGE (route)-[:DESTINATION]->(destination)
            """, route_id=route.route_id, properties=payload, origin_id=route.origin_id, destination_id=route.destination_id).consume()
            for leg in route.legs:
                properties = leg.model_dump(mode="json")
                properties["geometry_json"] = json_text(properties.pop("geometry", []))
                properties["evidence_refs_json"] = json_text(properties.pop("evidence_refs", []))
                transaction.run("""
                    MATCH (route:VehicleRoute {route_id:$route_id})
                    MERGE (leg:RouteLeg {leg_id:$leg_id}) SET leg += $properties
                    MERGE (route)-[relationship:HAS_LEG]->(leg) SET relationship.sequence=$sequence
                    WITH leg
                    MATCH (origin) WHERE coalesce(origin.location_id,origin.unlocode,origin.code,origin.id)=$origin_id
                    MATCH (destination) WHERE coalesce(destination.location_id,destination.unlocode,destination.code,destination.id)=$destination_id
                    MERGE (leg)-[:FROM_NODE]->(origin)
                    MERGE (leg)-[:TO_NODE]->(destination)
                """, route_id=route.route_id, leg_id=leg.leg_id, properties=properties, sequence=leg.sequence,
                     origin_id=leg.origin_id, destination_id=leg.destination_id).consume()
            if route.risk:
                snapshot_id = f"risk_{route.route_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
                transaction.run("""
                    MATCH (route:VehicleRoute {route_id:$route_id})
                    MERGE (snapshot:RiskSnapshot {snapshot_id:$snapshot_id})
                    SET snapshot += $properties,snapshot.calculated_at=datetime()
                    MERGE (route)-[:HAS_RISK_SNAPSHOT]->(snapshot)
                """, route_id=route.route_id, snapshot_id=snapshot_id,
                     properties={**route.risk.model_dump(mode="json"), "risk_factors_json": json_text(route.risk.risk_factors), "evidence_refs_json": json_text(route.risk.evidence_refs)}).consume()
            if route.estimated_cost:
                estimate_id = f"cost_{route.route_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
                cost_properties = route.estimated_cost.model_dump(mode="json")
                cost_properties["input_snapshot_json"] = json_text(cost_properties.pop("input_snapshot"))
                transaction.run("""
                    MATCH (route:VehicleRoute {route_id:$route_id})
                    MERGE (estimate:CostEstimate {estimate_id:$estimate_id})
                    SET estimate += $properties,estimate.calculated_at=datetime()
                    MERGE (route)-[:HAS_COST_ESTIMATE]->(estimate)
                """, route_id=route.route_id, estimate_id=estimate_id,
                     properties=cost_properties).consume()
            if job_id:
                transaction.run("""MATCH (job:IngestionJob {job_id:$job_id}),(route:VehicleRoute {route_id:$route_id}) MERGE (job)-[:GENERATED]->(route)""", job_id=job_id, route_id=route.route_id).consume()

        self._execute_write(write)

    def search_routes(self, origin: str, destination: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._execute_read("""
            MATCH (route:VehicleRoute)-[:ORIGIN]->(origin)
            MATCH (route)-[:DESTINATION]->(destination)
            WHERE route.deleted_at IS NULL
              AND coalesce(origin.location_id,origin.unlocode,origin.code,origin.id)=$origin
              AND coalesce(destination.location_id,destination.unlocode,destination.code,destination.id)=$destination
            OPTIONAL MATCH (route)-[membership:HAS_LEG]->(leg:RouteLeg)
            OPTIONAL MATCH (route)-[:HAS_RISK_SNAPSHOT]->(risk:RiskSnapshot)
            OPTIONAL MATCH (route)-[:HAS_COST_ESTIMATE]->(cost:CostEstimate)
            WITH route,leg,membership,risk,cost ORDER BY membership.sequence,risk.calculated_at DESC,cost.calculated_at DESC
            RETURN properties(route) AS route,collect(DISTINCT properties(leg)) AS legs,
                   head(collect(DISTINCT properties(risk))) AS risk,head(collect(DISTINCT properties(cost))) AS cost
            ORDER BY route.score DESC LIMIT $limit
        """, {"origin": origin, "destination": destination, "limit": limit})

    def get_route(self, route_id: str) -> dict[str, Any] | None:
        rows = self._execute_read("""
            MATCH (route:VehicleRoute {route_id:$route_id})
            OPTIONAL MATCH (route)-[membership:HAS_LEG]->(leg:RouteLeg)
            WITH route,leg,membership ORDER BY membership.sequence
            RETURN properties(route) AS route,collect(properties(leg)) AS legs
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
            SET evidence += $properties,evidence.collected_at=datetime()
            WITH evidence
            OPTIONAL MATCH (entity) WHERE coalesce(entity.route_id,entity.leg_id,entity.location_id,entity.evidence_id)=$entity_id
            FOREACH (_ IN CASE WHEN entity IS NULL THEN [] ELSE [1] END | MERGE (entity)-[:SUPPORTED_BY]->(evidence))
        """, evidence_id=evidence_id, entity_id=request.entity_id,
             properties=request.model_dump(mode="json", exclude={"entity_id", "entity_type"})).consume())
        return evidence_id
