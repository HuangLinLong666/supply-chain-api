from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neo4j import READ_ACCESS, WRITE_ACCESS

from database.neo4j_client import close_driver, get_driver, get_settings, to_jsonable
from database.unified_schema import (
    ALL_MIGRATION_LABELS,
    IDENTITY_CONSTRAINTS,
    MIGRATION_CONFIRMATION,
    QUERY_INDEXES,
    SCHEMA_VERSION,
    TARGET_LABELS,
)


WAREHOUSE_LABELS = {
    "Warehouse",
    "ExportWarehouse",
    "OverseasWarehouse",
    "DepartureWarehouse",
    "ArrivalWarehouse",
}
LOCATION_KIND_ORDER = (
    ("Port", "port"),
    ("Airport", "airport"),
    ("RailTerminal", "rail_terminal"),
    ("RoadTerminal", "road_terminal"),
    ("Factory", "factory"),
)
LOCATION_ID_FIELDS = {
    "port": ("location_id", "unlocode", "port_id", "code", "id"),
    "airport": ("location_id", "iata", "iata_code", "icao", "airportId", "code", "id"),
    "rail_terminal": ("location_id", "terminal_code", "code", "id", "name"),
    "road_terminal": ("location_id", "road_terminal_id", "terminal_code", "code", "id", "name"),
    "factory": ("location_id", "factory_id", "factoryId", "factoryCode", "code", "id", "name"),
    "warehouse": ("location_id", "warehouse_id", "code", "airportId", "id"),
}
WAREHOUSE_ID_FIELDS = ("warehouse_id", "code", "airportId", "id")


@dataclass(frozen=True)
class IdentityProjection:
    entity: str
    labels: tuple[str, ...]
    expression: str


@dataclass(frozen=True)
class MigrationOperation:
    name: str
    description: str
    count_query: str
    write_query: str


IDENTITY_PROJECTIONS = (
    IdentityProjection("Supplier", ("Supplier",), "node.supplier_id"),
    IdentityProjection("Factory", ("Factory",), "node.factory_id"),
    IdentityProjection("RouteSegment", ("RouteSegment", "RouteLeg"), "coalesce(node.segment_id,node.leg_id,node.segmentId)"),
    IdentityProjection("Route", ("Route", "VehicleRoute"), "node.route_id"),
    IdentityProjection("GeoZone", ("GeoZone", "NewsRiskZone"), "node.zone_id"),
    IdentityProjection(
        "RiskObservation",
        ("RiskObservation", "RiskSnapshot", "WeatherRiskSnapshot", "CountryRiskObservation", "NewsRiskEvent"),
        "coalesce(node.observation_id,node.snapshot_id,node.article_id)",
    ),
    IdentityProjection(
        "CostObservation",
        ("CostObservation", "CostEstimate", "RouteCostObservation"),
        "coalesce(node.observation_id,node.estimate_id)",
    ),
    IdentityProjection(
        "DelayObservation",
        ("DelayObservation", "RouteDelayObservation"),
        "node.observation_id",
    ),
    IdentityProjection(
        "Evidence",
        ("Evidence", "SourceEvidence", "NewsRiskEvent"),
        "coalesce(node.evidence_id,node.article_id)",
    ),
    IdentityProjection("Vessel", ("Vessel",), "node.mmsi"),
    IdentityProjection("PortTrafficSnapshot", ("PortTrafficSnapshot",), "node.snapshot_id"),
    IdentityProjection("RecommendationSnapshot", ("RecommendationSnapshot",), "node.snapshot_id"),
)


ALIAS_OPERATIONS = (
    MigrationOperation(
        "route_leg_to_segment",
        "为 RouteLeg 增加 RouteSegment 规范标签与 segment_id，保留 RouteLeg/leg_id。",
        "MATCH (node:RouteLeg) WHERE NOT node:RouteSegment OR node.segment_id IS NULL RETURN count(node) AS count",
        """
        MATCH (node:RouteLeg)
        WHERE NOT node:RouteSegment OR node.segment_id IS NULL
        SET node:RouteSegment,node.segment_id=coalesce(node.segment_id,node.leg_id)
        RETURN count(node) AS count
        """,
    ),
    MigrationOperation(
        "vehicle_route_to_route",
        "为 VehicleRoute 增加 Route 规范标签，保留 VehicleRoute。",
        "MATCH (node:VehicleRoute) WHERE NOT node:Route RETURN count(node) AS count",
        "MATCH (node:VehicleRoute) WHERE NOT node:Route SET node:Route RETURN count(node) AS count",
    ),
    MigrationOperation(
        "news_zone_to_geo_zone",
        "为 NewsRiskZone 增加 GeoZone 规范标签。",
        "MATCH (node:NewsRiskZone) WHERE NOT node:GeoZone RETURN count(node) AS count",
        "MATCH (node:NewsRiskZone) WHERE NOT node:GeoZone SET node:GeoZone RETURN count(node) AS count",
    ),
    MigrationOperation(
        "route_risk_snapshot_to_observation",
        "将路线风险快照作为 RiskObservation 兼容读取。",
        "MATCH (node:RiskSnapshot) WHERE NOT node:RiskObservation OR node.observation_id IS NULL RETURN count(node) AS count",
        """
        MATCH (node:RiskSnapshot)
        WHERE NOT node:RiskObservation OR node.observation_id IS NULL
        SET node:RiskObservation,
            node.observation_id=coalesce(node.observation_id,node.snapshot_id),
            node.observation_type=coalesce(node.observation_type,'route_risk_snapshot'),
            node.observed_at=coalesce(node.observed_at,node.calculated_at)
        RETURN count(node) AS count
        """,
    ),
    MigrationOperation(
        "weather_snapshot_to_observation",
        "将 Open-Meteo 天气快照作为 RiskObservation，保留 WeatherRiskSnapshot。",
        "MATCH (node:WeatherRiskSnapshot) WHERE NOT node:RiskObservation OR node.observation_id IS NULL RETURN count(node) AS count",
        """
        MATCH (node:WeatherRiskSnapshot)
        WHERE NOT node:RiskObservation OR node.observation_id IS NULL
        SET node:RiskObservation,
            node.observation_id=coalesce(node.observation_id,node.snapshot_id),
            node.observation_type=coalesce(node.observation_type,'weather_risk'),
            node.provider=coalesce(node.provider,'Open-Meteo'),
            node.source_type=coalesce(node.source_type,'open_api')
        RETURN count(node) AS count
        """,
    ),
    MigrationOperation(
        "country_risk_to_observation",
        "为 CountryRiskObservation 增加 RiskObservation 规范标签。",
        "MATCH (node:CountryRiskObservation) WHERE NOT node:RiskObservation RETURN count(node) AS count",
        """
        MATCH (node:CountryRiskObservation)
        WHERE NOT node:RiskObservation
        SET node:RiskObservation,node.observation_type=coalesce(node.observation_type,'country_risk')
        RETURN count(node) AS count
        """,
    ),
    MigrationOperation(
        "news_event_to_observation_and_evidence",
        "将 GDELT 新闻事件同时标记为可追溯风险观测和证据。",
        """
        MATCH (node:NewsRiskEvent)
        WHERE NOT node:RiskObservation OR NOT node:Evidence
           OR node.observation_id IS NULL OR node.evidence_id IS NULL
        RETURN count(node) AS count
        """,
        """
        MATCH (node:NewsRiskEvent)
        WHERE NOT node:RiskObservation OR NOT node:Evidence
           OR node.observation_id IS NULL OR node.evidence_id IS NULL
        SET node:RiskObservation:Evidence,
            node.observation_id=coalesce(node.observation_id,node.article_id),
            node.evidence_id=coalesce(node.evidence_id,node.article_id),
            node.observation_type=coalesce(node.observation_type,'news_event_risk'),
            node.evidence_type=coalesce(node.evidence_type,'news_article'),
            node.observed_at=coalesce(node.observed_at,node.seen_at),
            node.collected_at=coalesce(node.collected_at,node.seen_at),
            node.source_url=coalesce(node.source_url,node.url),
            node.provider=coalesce(node.provider,'GDELT'),
            node.source_type=coalesce(node.source_type,'open_api')
        RETURN count(node) AS count
        """,
    ),
    MigrationOperation(
        "cost_estimate_to_observation",
        "为 CostEstimate 增加 CostObservation 规范标签。",
        "MATCH (node:CostEstimate) WHERE NOT node:CostObservation OR node.observation_id IS NULL RETURN count(node) AS count",
        """
        MATCH (node:CostEstimate)
        WHERE NOT node:CostObservation OR node.observation_id IS NULL
        SET node:CostObservation,
            node.observation_id=coalesce(node.observation_id,node.estimate_id),
            node.observation_type=coalesce(node.observation_type,'route_cost_estimate'),
            node.observed_at=coalesce(node.observed_at,node.calculated_at)
        RETURN count(node) AS count
        """,
    ),
    MigrationOperation(
        "route_cost_to_observation",
        "为 RouteCostObservation 增加 CostObservation 规范标签。",
        "MATCH (node:RouteCostObservation) WHERE NOT node:CostObservation RETURN count(node) AS count",
        """
        MATCH (node:RouteCostObservation)
        WHERE NOT node:CostObservation
        SET node:CostObservation,node.observation_type=coalesce(node.observation_type,'route_cost')
        RETURN count(node) AS count
        """,
    ),
    MigrationOperation(
        "route_delay_to_observation",
        "为 RouteDelayObservation 增加 DelayObservation 规范标签。",
        "MATCH (node:RouteDelayObservation) WHERE NOT node:DelayObservation RETURN count(node) AS count",
        """
        MATCH (node:RouteDelayObservation)
        WHERE NOT node:DelayObservation
        SET node:DelayObservation,node.observation_type=coalesce(node.observation_type,'route_delay')
        RETURN count(node) AS count
        """,
    ),
    MigrationOperation(
        "source_evidence_to_evidence",
        "为 SourceEvidence 增加 Evidence 规范标签。",
        "MATCH (node:SourceEvidence) WHERE NOT node:Evidence RETURN count(node) AS count",
        "MATCH (node:SourceEvidence) WHERE NOT node:Evidence SET node:Evidence RETURN count(node) AS count",
    ),
)


RELATIONSHIP_OPERATIONS = (
    MigrationOperation(
        "canonical_route_segments",
        "为 HAS_LEG 增加 HAS_SEGMENT 兼容关系。",
        """
        MATCH (route)-[:HAS_LEG]->(segment)
        WHERE (route:Route OR route:VehicleRoute)
          AND (segment:RouteSegment OR segment:RouteLeg)
          AND NOT EXISTS { MATCH (route)-[:HAS_SEGMENT]->(segment) }
        RETURN count(*) AS count
        """,
        """
        MATCH (route:Route)-[legacy:HAS_LEG]->(segment:RouteSegment)
        WHERE NOT EXISTS { MATCH (route)-[:HAS_SEGMENT]->(segment) }
        MERGE (route)-[canonical:HAS_SEGMENT]->(segment)
        SET canonical.sequence=legacy.sequence,canonical.schema_version=$schema_version
        RETURN count(*) AS count
        """,
    ),
    MigrationOperation(
        "canonical_route_risk_observations",
        "为 HAS_RISK_SNAPSHOT 增加 HAS_RISK_OBSERVATION。",
        """
        MATCH (route)-[:HAS_RISK_SNAPSHOT]->(observation:RiskSnapshot)
        WHERE (route:Route OR route:VehicleRoute)
          AND NOT EXISTS { MATCH (route)-[:HAS_RISK_OBSERVATION]->(observation) }
        RETURN count(*) AS count
        """,
        """
        MATCH (route:Route)-[:HAS_RISK_SNAPSHOT]->(observation:RiskObservation)
        WHERE NOT EXISTS { MATCH (route)-[:HAS_RISK_OBSERVATION]->(observation) }
        MERGE (route)-[relationship:HAS_RISK_OBSERVATION]->(observation)
        SET relationship.schema_version=$schema_version
        RETURN count(*) AS count
        """,
    ),
    MigrationOperation(
        "canonical_route_cost_observations",
        "为 HAS_COST_ESTIMATE 增加 HAS_COST_OBSERVATION。",
        """
        MATCH (route)-[:HAS_COST_ESTIMATE]->(observation:CostEstimate)
        WHERE (route:Route OR route:VehicleRoute)
          AND NOT EXISTS { MATCH (route)-[:HAS_COST_OBSERVATION]->(observation) }
        RETURN count(*) AS count
        """,
        """
        MATCH (route:Route)-[:HAS_COST_ESTIMATE]->(observation:CostObservation)
        WHERE NOT EXISTS { MATCH (route)-[:HAS_COST_OBSERVATION]->(observation) }
        MERGE (route)-[relationship:HAS_COST_OBSERVATION]->(observation)
        SET relationship.schema_version=$schema_version
        RETURN count(*) AS count
        """,
    ),
    MigrationOperation(
        "canonical_port_weather_observations",
        "为 HAS_WEATHER_SNAPSHOT 增加 HAS_RISK_OBSERVATION。",
        """
        MATCH (port:Port)-[:HAS_WEATHER_SNAPSHOT]->(observation:WeatherRiskSnapshot)
        WHERE NOT EXISTS { MATCH (port)-[:HAS_RISK_OBSERVATION]->(observation) }
        RETURN count(*) AS count
        """,
        """
        MATCH (port:Port)-[:HAS_WEATHER_SNAPSHOT]->(observation:RiskObservation)
        WHERE NOT EXISTS { MATCH (port)-[:HAS_RISK_OBSERVATION]->(observation) }
        MERGE (port)-[relationship:HAS_RISK_OBSERVATION]->(observation)
        SET relationship.schema_version=$schema_version
        RETURN count(*) AS count
        """,
    ),
)


NORMALIZE_QUERY = """
MATCH (node)
WHERE any(label IN labels(node) WHERE label IN $target_labels)
  AND (coalesce(node.schema_version,'') <> $schema_version OR node.data_status IS NULL)
WITH node,
     coalesce(node.source,node.data_source,node.dataSource,node.vehicle_network_source,
              node.modelSource,node.source_repo,node.weather_source,node.marine_source) AS normalized_source,
     coalesce(node.source_type,node.vehicle_network_source_type) AS existing_source_type,
     coalesce(node.confidence,node.confidence_score,node.vehicle_network_confidence) AS normalized_confidence,
     coalesce(node.collected_at,node.fetched_at,node.observed_at,node.seen_at,node.created_at,
              node.updated_at,node.last_updated,node.calculated_at) AS normalized_collected_at
WITH node,normalized_source,normalized_confidence,normalized_collected_at,
     toLower(toString(coalesce(normalized_source,''))) AS source_text,
     toLower(toString(coalesce(existing_source_type,''))) AS source_type_text
WITH node,normalized_source,normalized_confidence,normalized_collected_at,source_text,
     CASE
       WHEN source_type_text <> '' THEN source_type_text
       WHEN source_text CONTAINS 'gdelt' OR source_text CONTAINS 'open-meteo'
         OR source_text CONTAINS 'open meteo' THEN 'open_api'
       WHEN source_text CONTAINS 'aisstream' THEN 'ais_observed'
       WHEN source_text CONTAINS 'official_registry' THEN 'official_registry'
       WHEN source_text CONTAINS 'official_schedule' THEN 'official_schedule'
       WHEN source_text CONTAINS 'estimated_by_graph' THEN 'estimated_by_graph'
       WHEN source_text CONTAINS 'fabricated_for_testing' OR source_text CONTAINS 'synthetic'
         OR source_text CONTAINS 'standard_skeleton_reference'
         OR source_text CONTAINS 'external_repos_reference_fields'
         OR source_text CONTAINS 'tesla sec route skeleton'
         OR source_text = 'sample' OR source_text = 'mock' THEN 'fabricated_for_testing'
       ELSE null
     END AS normalized_source_type
WITH node,normalized_source,normalized_confidence,normalized_collected_at,source_text,normalized_source_type,
     CASE
       WHEN toLower(toString(coalesce(node.review_status,''))) IN ['approved','verified','reviewed'] THEN 'verified'
       WHEN normalized_source_type IN ['official_registry','official_schedule'] THEN 'verified'
       WHEN normalized_source_type IN ['open_api','paid_api','ais_observed','flight_observed'] THEN 'observed'
       WHEN normalized_source_type='estimated_by_graph' OR node.is_inferred=true THEN 'estimated'
       WHEN normalized_source_type='fabricated_for_testing' THEN 'synthetic'
       ELSE 'unavailable'
     END AS normalized_data_status,
     CASE
       WHEN source_text CONTAINS 'gdelt' THEN 'GDELT'
       WHEN source_text CONTAINS 'open-meteo' OR source_text CONTAINS 'open meteo' THEN 'Open-Meteo'
       WHEN source_text CONTAINS 'aisstream' THEN 'AISStream.io'
       WHEN normalized_source_type IN ['official_registry','official_schedule','open_api','paid_api']
         THEN toString(normalized_source)
       ELSE null
     END AS normalized_provider
SET node.schema_version=$schema_version,
    node.schema_migrated_at=coalesce(node.schema_migrated_at,datetime($migrated_at)),
    node.source=coalesce(node.source,normalized_source),
    node.source_type=coalesce(node.source_type,normalized_source_type),
    node.provider=coalesce(node.provider,normalized_provider),
    node.source_url=coalesce(node.source_url,node.url),
    node.collected_at=coalesce(node.collected_at,normalized_collected_at),
    node.confidence=coalesce(node.confidence,normalized_confidence),
    node.is_inferred=coalesce(
      node.is_inferred,
      CASE WHEN normalized_source_type='estimated_by_graph' THEN true ELSE null END
    ),
    node.data_status=coalesce(node.data_status,normalized_data_status)
FOREACH (_ IN CASE WHEN node:RiskObservation OR node:CostObservation OR node:DelayObservation THEN [1] ELSE [] END |
  SET node.status=coalesce(
    node.status,
    CASE
      WHEN normalized_provider IS NOT NULL AND normalized_data_status IN ['verified','observed'] THEN 'available'
      ELSE 'unavailable'
    END
  )
)
RETURN count(node) AS count
"""


ROUTE_SEGMENT_METADATA_QUERY = """
MATCH (segment:RouteSegment)
WITH segment,toLower(toString(coalesce(segment.mode,segment.routeMode,''))) AS raw_mode
WITH segment,raw_mode,
     CASE
       WHEN raw_mode IN ['sea','ocean','maritime','ship','shipping','sea_freight','ocean_freight'] THEN 'sea'
       WHEN raw_mode IN ['air','flight','air_freight'] THEN 'air'
       WHEN raw_mode IN ['rail','train','railway','rail_freight'] THEN 'rail'
       WHEN raw_mode IN ['road','truck','trucking','highway','road_freight'] THEN 'road'
       ELSE null
     END AS canonical_mode
WHERE segment.scoring_version IS NULL OR segment.feasibility_status IS NULL
   OR segment.validity_status IS NULL OR (segment.canonical_mode IS NULL AND canonical_mode IS NOT NULL)
SET segment.legacy_mode=coalesce(segment.legacy_mode,CASE WHEN raw_mode='' THEN null ELSE raw_mode END),
    segment.canonical_mode=coalesce(segment.canonical_mode,canonical_mode),
    segment.scoring_version=coalesce(segment.scoring_version,'legacy_unversioned'),
    segment.feasibility_status=coalesce(
      segment.feasibility_status,
      CASE WHEN canonical_mode IS NULL THEN 'invalid_or_ambiguous_mode' ELSE 'unreviewed' END
    ),
    segment.validity_status=coalesce(
      segment.validity_status,
      CASE WHEN segment.valid_from IS NULL AND segment.valid_until IS NULL THEN 'unavailable' ELSE 'available' END
    )
RETURN count(segment) AS count
"""


ROUTE_METADATA_QUERY = """
MATCH (route:Route)
WHERE route.scoring_version IS NULL OR route.validity_status IS NULL
SET route.scoring_version=coalesce(route.scoring_version,'legacy_unversioned'),
    route.validity_status=coalesce(
      route.validity_status,
      CASE WHEN route.valid_from IS NULL AND route.valid_until IS NULL THEN 'unavailable' ELSE 'available' END
    )
RETURN count(route) AS count
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_identifier(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def stable_suffix(node: dict[str, Any]) -> str:
    payload = "|".join(
        (
            str(node.get("element_id") or ""),
            ",".join(sorted(str(label) for label in node.get("labels", []))),
            str(node.get("properties", {}).get("name") or ""),
            str(node.get("properties", {}).get("city") or ""),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized[:48] or "unnamed"


def location_kind(labels: Iterable[str]) -> str:
    label_set = set(labels)
    for label, kind in LOCATION_KIND_ORDER:
        if label in label_set:
            return kind
    if label_set & WAREHOUSE_LABELS:
        return "warehouse"
    raise ValueError(f"无法确定地点类型: {sorted(label_set)}")


def first_property(properties: dict[str, Any], fields: Iterable[str]) -> tuple[str | None, str | None]:
    for field in fields:
        value = normalize_identifier(properties.get(field))
        if value:
            return value, field
    return None, None


def build_location_plan(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    used_location_ids: dict[str, str] = {}
    used_warehouse_ids: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    ordered = sorted(
        nodes,
        key=lambda node: (
            0 if normalize_identifier(node.get("properties", {}).get("location_id")) else 1,
            str(node.get("element_id")),
        ),
    )
    for node in ordered:
        element_id = str(node["element_id"])
        labels = [str(label) for label in node.get("labels", [])]
        properties = node.get("properties", {})
        kind = location_kind(labels)
        preferred, detected_identity_source = first_property(properties, LOCATION_ID_FIELDS[kind])
        identity_source = normalize_identifier(properties.get("canonical_identity_source")) or detected_identity_source
        if not preferred:
            preferred = f"loc:{kind}:{stable_suffix(node)}"
            identity_source = "generated_stable_fallback"
        canonical_id = preferred
        identity_status = "canonical"
        if preferred in used_location_ids and used_location_ids[preferred] != element_id:
            canonical_id = f"{preferred}~{stable_suffix(node)[:8]}"
            identity_status = "conflict_disambiguated"
            conflicts.append(
                {
                    "entity": "TransportLocation",
                    "conflict_key": preferred,
                    "kept_by_element_id": used_location_ids[preferred],
                    "disambiguated_element_id": element_id,
                    "assigned_id": canonical_id,
                }
            )
        used_location_ids[canonical_id] = element_id
        desired: dict[str, Any] = {
            "location_id": canonical_id,
            "location_kind": kind,
            "canonical_identity_source": identity_source,
        }
        if identity_status != "canonical":
            desired["identity_status"] = identity_status
            desired["identity_conflict_key"] = preferred
        if kind == "warehouse":
            warehouse_id, detected_warehouse_source = first_property(properties, WAREHOUSE_ID_FIELDS)
            warehouse_source = normalize_identifier(properties.get("warehouse_identity_source")) or detected_warehouse_source
            if not warehouse_id:
                name = normalize_identifier(properties.get("name")) or stable_suffix(node)
                warehouse_id = f"warehouse:{slug(name)}"
                warehouse_source = "generated_from_name"
            if warehouse_id in used_warehouse_ids and used_warehouse_ids[warehouse_id] != element_id:
                warehouse_id = f"{warehouse_id}~{stable_suffix(node)[:8]}"
            used_warehouse_ids[warehouse_id] = element_id
            desired["warehouse_id"] = warehouse_id
            desired["warehouse_identity_source"] = warehouse_source
        updates = {key: value for key, value in desired.items() if properties.get(key) != value}
        labels_to_add = [label for label in ("TransportLocation", "Warehouse" if kind == "warehouse" else None) if label and label not in labels]
        if updates or labels_to_add:
            rows.append(
                {
                    "element_id": element_id,
                    "current_labels": labels,
                    "labels_to_add": labels_to_add,
                    "properties": updates,
                    "location_id": canonical_id,
                    "location_kind": kind,
                }
            )
    return {
        "rows": rows,
        "conflicts_resolved": conflicts,
        "projected_location_count": len(nodes),
        "projected_unique_location_ids": len(used_location_ids),
        "projected_warehouse_count": sum(1 for node in nodes if location_kind(node.get("labels", [])) == "warehouse"),
        "projected_unique_warehouse_ids": len(used_warehouse_ids),
    }


def schema_rule_present(rule: Any, records: list[dict[str, Any]]) -> bool:
    return any(
        record.get("entityType") == "NODE"
        and record.get("labelsOrTypes") == [rule.label]
        and record.get("properties") == [rule.property]
        for record in records
    )


def label_predicate(labels: Iterable[str], variable: str = "node") -> str:
    return " OR ".join(f"{variable}:{label}" for label in labels)


class MigrationRepository:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _session_options(self, access_mode: str) -> dict[str, Any]:
        options: dict[str, Any] = {
            "default_access_mode": access_mode,
            "notifications_min_severity": "OFF",
        }
        if self.settings.database:
            options["database"] = self.settings.database
        return options

    def read(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with get_driver().session(**self._session_options(READ_ACCESS)) as session:
            return [to_jsonable(record.data()) for record in session.run(query, parameters or {})]

    def totals(self) -> dict[str, int]:
        rows = self.read(
            """
            CALL { MATCH (node) RETURN count(node) AS nodes }
            CALL { MATCH ()-[relationship]->() RETURN count(relationship) AS relationships }
            RETURN nodes,relationships
            """
        )
        return {
            "nodes": int(rows[0]["nodes"]),
            "relationships": int(rows[0]["relationships"]),
        }

    def label_counts(self) -> dict[str, int]:
        rows = self.read(
            """
            MATCH (node)
            UNWIND [label IN labels(node) WHERE label IN $labels] AS label
            RETURN label,count(*) AS count ORDER BY label
            """,
            {"labels": list(ALL_MIGRATION_LABELS)},
        )
        counts = {label: 0 for label in ALL_MIGRATION_LABELS}
        counts.update({str(row["label"]): int(row["count"]) for row in rows})
        return counts

    def schema(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "constraints": self.read(
                "SHOW CONSTRAINTS YIELD name,type,entityType,labelsOrTypes,properties RETURN name,type,entityType,labelsOrTypes,properties"
            ),
            "indexes": self.read(
                "SHOW INDEXES YIELD name,type,entityType,labelsOrTypes,properties,state RETURN name,type,entityType,labelsOrTypes,properties,state"
            ),
        }

    def location_nodes(self) -> list[dict[str, Any]]:
        labels = tuple(label for label, _ in LOCATION_KIND_ORDER) + tuple(sorted(WAREHOUSE_LABELS))
        return self.read(
            f"""
            MATCH (node)
            WHERE {label_predicate(labels)}
            RETURN elementId(node) AS element_id,labels(node) AS labels,properties(node) AS properties
            ORDER BY element_id
            """
        )

    def identity_audit(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for projection in IDENTITY_PROJECTIONS:
            predicate = label_predicate(projection.labels)
            rows = self.read(
                f"""
                MATCH (node)
                WHERE {predicate}
                WITH node,{projection.expression} AS projected_id
                WITH count(node) AS total,
                     count(projected_id) AS with_id,
                     collect(CASE WHEN projected_id IS NULL THEN elementId(node) END)[0..20] AS missing_samples
                RETURN total,with_id,[item IN missing_samples WHERE item IS NOT NULL] AS missing_samples
                """
            )
            duplicates = self.read(
                f"""
                MATCH (node)
                WHERE {predicate}
                WITH toString({projection.expression}) AS projected_id,collect(elementId(node)) AS element_ids
                WHERE projected_id IS NOT NULL AND size(element_ids)>1
                RETURN projected_id,element_ids ORDER BY projected_id LIMIT 50
                """
            )
            row = rows[0] if rows else {"total": 0, "with_id": 0, "missing_samples": []}
            result.append(
                {
                    "entity": projection.entity,
                    "projected_nodes": int(row["total"]),
                    "with_identity": int(row["with_id"]),
                    "missing_identity": int(row["total"]) - int(row["with_id"]),
                    "missing_samples": row["missing_samples"],
                    "duplicate_identities": duplicates,
                }
            )
        return result

    def operation_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for operation in (*ALIAS_OPERATIONS, *RELATIONSHIP_OPERATIONS):
            rows = self.read(operation.count_query)
            counts[operation.name] = int(rows[0]["count"] if rows else 0)
        normalization_rows = self.read(
            """
            MATCH (node)
            WHERE any(label IN labels(node) WHERE label IN $labels)
              AND (coalesce(node.schema_version,'') <> $schema_version OR node.data_status IS NULL)
            RETURN count(DISTINCT node) AS count
            """,
            {"labels": list(ALL_MIGRATION_LABELS), "schema_version": SCHEMA_VERSION},
        )
        counts["normalize_provenance"] = int(normalization_rows[0]["count"] if normalization_rows else 0)
        segment_rows = self.read(
            """
            MATCH (segment)
            WHERE (segment:RouteSegment OR segment:RouteLeg)
            WITH segment,toLower(toString(coalesce(segment.mode,segment.routeMode,''))) AS raw_mode
            WHERE segment.scoring_version IS NULL OR segment.feasibility_status IS NULL
               OR segment.validity_status IS NULL
               OR (
                 segment.canonical_mode IS NULL
                 AND raw_mode IN [
                   'sea','ocean','maritime','ship','shipping','sea_freight','ocean_freight',
                   'air','flight','air_freight','rail','train','railway','rail_freight',
                   'road','truck','trucking','highway','road_freight'
                 ]
               )
            RETURN count(DISTINCT segment) AS count
            """
        )
        counts["normalize_route_segments"] = int(segment_rows[0]["count"] if segment_rows else 0)
        route_rows = self.read(
            """
            MATCH (route)
            WHERE (route:Route OR route:VehicleRoute)
              AND (route.scoring_version IS NULL OR route.validity_status IS NULL)
            RETURN count(DISTINCT route) AS count
            """
        )
        counts["normalize_routes"] = int(route_rows[0]["count"] if route_rows else 0)
        return counts

    def ensure_schema(self, missing_constraints: list[str], missing_indexes: list[str]) -> dict[str, int]:
        statements = {rule.name: rule.statement for rule in IDENTITY_CONSTRAINTS}
        statements.update({rule.name: rule.statement for rule in QUERY_INDEXES})
        with get_driver().session(**self._session_options(WRITE_ACCESS)) as session:
            for name in (*missing_constraints, *missing_indexes):
                session.run(statements[name]).consume()
        return {"constraints_created": len(missing_constraints), "indexes_created": len(missing_indexes)}

    def execute_data(self, location_rows: list[dict[str, Any]], migrated_at: datetime) -> dict[str, Any]:
        parameters = {
            "schema_version": SCHEMA_VERSION,
            "migrated_at": migrated_at.isoformat(),
            "target_labels": list(TARGET_LABELS),
        }

        def write(transaction):
            operations: dict[str, dict[str, int]] = {}
            counter_totals: Counter[str] = Counter()

            def run(name: str, query: str, query_parameters: dict[str, Any] | None = None) -> None:
                result = transaction.run(query, query_parameters or parameters)
                record = result.single()
                summary = result.consume()
                counters = summary.counters
                operation_counters = {
                    "matched": int(record["count"] if record else 0),
                    "labels_added": int(counters.labels_added),
                    "properties_set": int(counters.properties_set),
                    "relationships_created": int(counters.relationships_created),
                }
                operations[name] = operation_counters
                counter_totals.update(operation_counters)

            if location_rows:
                run(
                    "normalize_locations",
                    """
                    UNWIND $rows AS row
                    MATCH (node) WHERE elementId(node)=row.element_id
                    SET node += row.properties
                    SET node:TransportLocation
                    FOREACH (_ IN CASE WHEN 'Warehouse' IN row.labels_to_add THEN [1] ELSE [] END | SET node:Warehouse)
                    RETURN count(node) AS count
                    """,
                    {"rows": location_rows},
                )
            else:
                operations["normalize_locations"] = {
                    "matched": 0,
                    "labels_added": 0,
                    "properties_set": 0,
                    "relationships_created": 0,
                }
            for operation in ALIAS_OPERATIONS:
                run(operation.name, operation.write_query)
            run("normalize_provenance", NORMALIZE_QUERY)
            run("normalize_route_segments", ROUTE_SEGMENT_METADATA_QUERY)
            run("normalize_routes", ROUTE_METADATA_QUERY)
            for operation in RELATIONSHIP_OPERATIONS:
                run(operation.name, operation.write_query)
            return {"operations": operations, "counter_totals": dict(counter_totals)}

        with get_driver().session(**self._session_options(WRITE_ACCESS)) as session:
            return session.execute_write(write)

    def coverage(self) -> list[dict[str, Any]]:
        return self.read(
            """
            MATCH (node)
            UNWIND [label IN labels(node) WHERE label IN $labels] AS label
            RETURN label,count(*) AS nodes,
                   count(node.schema_version) AS with_schema_version,
                   count(node.data_status) AS with_data_status,
                   count(node.source) AS with_source,
                   count(node.provider) AS with_provider,
                   count(node.collected_at) AS with_collected_at,
                   count(node.confidence) AS with_confidence
            ORDER BY label
            """,
            {"labels": list(TARGET_LABELS)},
        )


def migration_plan_fingerprint(
    location_plan: dict[str, Any], operation_counts: dict[str, int], missing_constraints: list[str], missing_indexes: list[str]
) -> str:
    payload = {
        "location_rows": [
            {
                "element_id": row["element_id"],
                "labels_to_add": row["labels_to_add"],
                "properties": row["properties"],
            }
            for row in location_plan["rows"]
        ],
        "operation_counts": operation_counts,
        "missing_constraints": missing_constraints,
        "missing_indexes": missing_indexes,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_无记录_\n"
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        values = [str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ") for value in row]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"


def render_report(artifact: dict[str, Any], json_path: Path, report_path: Path) -> str:
    execution = artifact["execution"]
    identity_rows = artifact["preflight"]["identity_audit"]
    operation_rows = artifact["plan"]["operation_counts"]
    target_after = artifact.get("after", {}).get("label_counts") or {}
    lines = [
        "# 阶段 3：Neo4j 统一数据模型迁移报告",
        "",
        f"> 生成时间（UTC）：`{artifact['metadata']['generated_at_utc']}`  ",
        f"> 模式：`{artifact['mode']}`  ",
        f"> Schema 版本：`{artifact['metadata']['schema_version']}`  ",
        f"> 数据库：`{artifact['metadata']['database']}`",
        "",
        "## 1. 执行结论",
        "",
        f"- 状态：`{execution['status']}`。",
        f"- 删除节点：**0**；删除关系：**0**。",
        f"- 计划指纹：`{artifact['metadata']['plan_sha256']}`。",
        f"- 预计规范化节点：**{artifact['plan']['projected_node_updates']}**。",
        f"- 预计新增兼容关系：**{artifact['plan']['projected_relationship_creates']}**。",
    ]
    if execution.get("counter_totals"):
        counters = execution["counter_totals"]
        lines.extend(
            [
                f"- 实际新增标签：**{counters.get('labels_added', 0)}**。",
                f"- 实际设置属性：**{counters.get('properties_set', 0)}**。",
                f"- 实际新增关系：**{counters.get('relationships_created', 0)}**。",
            ]
        )
    lines.extend(
        [
            "",
            "## 2. 安全策略",
            "",
            "- 本迁移不执行 `DELETE`、`DETACH DELETE` 或节点重建。",
            "- 保留所有旧标签、旧主键和旧关系，只增加规范标签、字段和兼容关系。",
            "- GDELT、Open-Meteo、AIS 节点不会被删除；其原标签继续供旧 API 使用。",
            "- 没有真实 Provider 的数据不会补造 Provider，`data_status` 会标记为 `estimated`、`synthetic` 或 `unavailable`。",
            "- 地点标识冲突不自动合并节点，而是分配可审计后缀并记录冲突，避免唯一约束失败。",
            "",
            "## 3. 主键预检",
            "",
            markdown_table(
                ["实体", "预计节点", "有主键", "缺主键", "重复主键组"],
                [
                    [row["entity"], row["projected_nodes"], row["with_identity"], row["missing_identity"], len(row["duplicate_identities"])]
                    for row in identity_rows
                ],
            ),
            "## 4. 地点统一",
            "",
            f"- 预计 `TransportLocation` 节点：**{artifact['plan']['location_plan']['projected_location_count']}**。",
            f"- 预计 `Warehouse` 节点：**{artifact['plan']['location_plan']['projected_warehouse_count']}**。",
            f"- 本次需要更新地点：**{len(artifact['plan']['location_plan']['rows'])}**。",
            f"- 已安全消歧的地点 ID 冲突：**{len(artifact['plan']['location_plan']['conflicts_resolved'])}**。",
            "",
            markdown_table(
                ["elementId", "当前标签", "新增标签", "location_id", "类型"],
                [
                    [row["element_id"], ", ".join(row["current_labels"]), ", ".join(row["labels_to_add"]), row["location_id"], row["location_kind"]]
                    for row in artifact["plan"]["location_plan"]["rows"][:40]
                ],
            ),
            "## 5. 迁移操作",
            "",
            markdown_table(["操作", "预计数量"], [[name, count] for name, count in operation_rows.items()]),
            "## 6. 约束与索引",
            "",
            f"- 待补唯一约束：**{len(artifact['plan']['missing_constraints'])}**。",
            f"- 待补查询索引：**{len(artifact['plan']['missing_indexes'])}**。",
            "",
            markdown_table(["类型", "名称"], [["约束", name] for name in artifact["plan"]["missing_constraints"]] + [["索引", name] for name in artifact["plan"]["missing_indexes"]]),
            "## 7. 统一模型数量",
            "",
            markdown_table(
                ["标签", "迁移前", "迁移后"],
                [[label, artifact["before"]["label_counts"].get(label, 0), target_after.get(label, "未执行")] for label in TARGET_LABELS],
            ),
            "## 8. 产物",
            "",
            f"- JSON：`{json_path}`。",
            f"- Markdown：`{report_path}`。",
            "",
            "## 9. 命令",
            "",
            "```bash",
            "python scripts/migrate_unified_schema.py --dry-run",
            "python scripts/migrate_unified_schema.py --execute --confirm APPLY_UNIFIED_SCHEMA_V1",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def timestamped_paths(output_dir: Path, generated_at: datetime) -> tuple[Path, Path]:
    timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    return (
        output_dir / f"unified_schema_migration_{timestamp}.json",
        output_dir / f"unified_schema_migration_{timestamp}.md",
    )


def write_artifacts(artifact: dict[str, Any], json_path: Path, report_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_report(artifact, json_path, report_path), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="以兼容方式统一 Neo4j 汽车供应链实体模型")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只生成迁移计划，不修改数据库（默认）")
    mode.add_argument("--execute", action="store_true", help="执行幂等 Schema 与数据迁移")
    parser.add_argument("--confirm", default="", help=f"执行时必须填写 {MIGRATION_CONFIRMATION}")
    parser.add_argument("--max-node-updates", type=int, default=25000, help="单次规范化节点上限")
    parser.add_argument("--max-relationship-creates", type=int, default=2000, help="单次新增兼容关系上限")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"), help="迁移报告输出目录")
    args = parser.parse_args()
    if args.max_node_updates < 0 or args.max_relationship_creates < 0:
        parser.error("迁移上限不能小于 0")
    if args.execute and args.confirm != MIGRATION_CONFIRMATION:
        parser.error(f"--execute 必须同时提供 --confirm {MIGRATION_CONFIRMATION}")
    return args


def main() -> int:
    args = parse_args()
    mode = "execute" if args.execute else "dry-run"
    generated_at = utc_now()
    json_path, report_path = timestamped_paths(args.output_dir, generated_at)
    repository = MigrationRepository()
    artifact: dict[str, Any] = {}
    try:
        get_driver().verify_connectivity()
        before_schema = repository.schema()
        before_counts = repository.label_counts()
        locations = repository.location_nodes()
        location_plan = build_location_plan(locations)
        identity_audit = repository.identity_audit()
        operation_counts = repository.operation_counts()
        missing_constraints = [
            rule.name for rule in IDENTITY_CONSTRAINTS if not schema_rule_present(rule, before_schema["constraints"])
        ]
        missing_indexes = [
            rule.name for rule in QUERY_INDEXES if not schema_rule_present(rule, before_schema["indexes"])
        ]
        blocking_conflicts = [
            {"entity": row["entity"], "duplicates": row["duplicate_identities"]}
            for row in identity_audit
            if row["duplicate_identities"]
        ]
        projected_node_updates = int(operation_counts.get("normalize_provenance", 0))
        projected_relationship_creates = sum(
            operation_counts.get(operation.name, 0) for operation in RELATIONSHIP_OPERATIONS
        )
        plan_sha256 = migration_plan_fingerprint(
            location_plan, operation_counts, missing_constraints, missing_indexes
        )
        artifact = {
            "metadata": {
                "generated_at_utc": generated_at.isoformat(),
                "schema_version": SCHEMA_VERSION,
                "database": repository.settings.database or "default",
                "credentials_redacted": True,
                "plan_sha256": plan_sha256,
            },
            "mode": mode,
            "safety": {
                "deletes_nodes": False,
                "deletes_relationships": False,
                "preserves_legacy_labels": True,
                "preserves_legacy_relationships": True,
                "uses_merge_for_compatibility_relationships": True,
            },
            "before": {
                "totals": repository.totals(),
                "label_counts": before_counts,
                "constraint_count": len(before_schema["constraints"]),
                "index_count": len(before_schema["indexes"]),
            },
            "preflight": {
                "identity_audit": identity_audit,
                "blocking_conflicts": blocking_conflicts,
                "location_conflicts_resolved": location_plan["conflicts_resolved"],
            },
            "plan": {
                "projected_node_updates": projected_node_updates,
                "projected_relationship_creates": projected_relationship_creates,
                "location_plan": location_plan,
                "operation_counts": operation_counts,
                "missing_constraints": missing_constraints,
                "missing_indexes": missing_indexes,
            },
            "execution": {
                "status": "dry_run" if mode == "dry-run" else "planned",
                "database_writes": 0,
                "nodes_deleted": 0,
                "relationships_deleted": 0,
            },
            "after": {},
        }
        write_artifacts(artifact, json_path, report_path)
        if args.execute:
            if blocking_conflicts:
                artifact["execution"].update(
                    {"status": "aborted_identity_conflicts", "error": "规范主键存在重复，未执行数据库写入"}
                )
                write_artifacts(artifact, json_path, report_path)
                raise SystemExit(2)
            if projected_node_updates > args.max_node_updates:
                artifact["execution"].update(
                    {
                        "status": "aborted_node_limit",
                        "error": f"预计节点 {projected_node_updates} 超过上限 {args.max_node_updates}",
                    }
                )
                write_artifacts(artifact, json_path, report_path)
                raise SystemExit(2)
            if projected_relationship_creates > args.max_relationship_creates:
                artifact["execution"].update(
                    {
                        "status": "aborted_relationship_limit",
                        "error": f"预计关系 {projected_relationship_creates} 超过上限 {args.max_relationship_creates}",
                    }
                )
                write_artifacts(artifact, json_path, report_path)
                raise SystemExit(2)
            schema_changes = repository.ensure_schema(missing_constraints, missing_indexes)
            data_changes = repository.execute_data(location_plan["rows"], generated_at)
            after_schema = repository.schema()
            artifact["execution"].update(
                {
                    "status": "completed",
                    "database_writes": data_changes["counter_totals"].get("properties_set", 0)
                    + data_changes["counter_totals"].get("labels_added", 0)
                    + data_changes["counter_totals"].get("relationships_created", 0)
                    + schema_changes["constraints_created"]
                    + schema_changes["indexes_created"],
                    **schema_changes,
                    **data_changes,
                }
            )
            artifact["after"] = {
                "totals": repository.totals(),
                "label_counts": repository.label_counts(),
                "constraint_count": len(after_schema["constraints"]),
                "index_count": len(after_schema["indexes"]),
                "coverage": repository.coverage(),
            }
            write_artifacts(artifact, json_path, report_path)
    finally:
        close_driver()
    print(
        json.dumps(
            {
                "mode": mode,
                "status": artifact["execution"]["status"],
                "database_writes": artifact["execution"]["database_writes"],
                "nodes_deleted": 0,
                "relationships_deleted": 0,
                "projected_node_updates": artifact["plan"]["projected_node_updates"],
                "projected_relationship_creates": artifact["plan"]["projected_relationship_creates"],
                "blocking_conflicts": len(artifact["preflight"]["blocking_conflicts"]),
                "json": str(json_path),
                "report": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
