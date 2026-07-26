from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neo4j import READ_ACCESS

from database.neo4j_client import close_driver, get_driver, get_settings, to_jsonable


AUDIT_VERSION = "backend-audit-v1"
LOCATION_LABELS = {
    "TransportLocation",
    "Port",
    "Airport",
    "Factory",
    "Warehouse",
    "RailTerminal",
    "RoadTerminal",
    "TransitHub",
    "EntryPoint",
    "DeparturePoint",
    "TransferPoint",
    "Destination",
}
KEY_ENTITY_LABELS = [
    "Supplier",
    "Factory",
    "Warehouse",
    "TransportLocation",
    "Port",
    "Airport",
    "RailTerminal",
    "RoadTerminal",
    "RouteSegment",
    "Route",
    "VehicleRoute",
    "RouteLeg",
    "GeoZone",
    "NewsRiskZone",
    "RiskObservation",
    "RiskSnapshot",
    "CostObservation",
    "RouteCostObservation",
    "DelayObservation",
    "RouteDelayObservation",
    "Evidence",
    "SourceEvidence",
    "Vessel",
    "VesselObservation",
    "PortTrafficSnapshot",
    "RecommendationSnapshot",
    "RouteRecommendation",
]
REALTIME_LABELS = {
    "NewsRiskEvent": ("seen_at", "updated_at", "collected_at"),
    "NewsRiskZone": ("updated_at", "collected_at"),
    "WeatherRiskSnapshot": ("fetched_at", "observed_at", "updated_at"),
    "VesselObservation": ("observed_at", "collected_at", "updated_at"),
    "PortTrafficSnapshot": ("observed_at", "collected_at", "updated_at"),
    "RouteCostObservation": ("observed_at", "collected_at", "updated_at"),
    "RouteDelayObservation": ("observed_at", "collected_at", "updated_at"),
    "CountryRiskObservation": ("observed_at", "collected_at", "updated_at"),
    "PortObservation": ("observed_at", "collected_at", "updated_at"),
}
SOURCE_FIELDS = (
    "provider",
    "source",
    "source_type",
    "data_source",
    "dataSource",
    "modelSource",
    "source_repo",
    "weather_source",
    "marine_source",
)
SYNTHETIC_MARKERS = (
    "fabricated_for_testing",
    "synthetic",
    "sample",
    "mock",
    "standard_skeleton_reference",
    "external_repos_reference_fields",
    "tesla sec route skeleton",
)
FORBIDDEN_CYPHER = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|LOAD\s+CSV|FOREACH)\b",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def source_status(*values: Any) -> str:
    text = " ".join(str(value or "") for value in values).casefold()
    if any(marker in text for marker in SYNTHETIC_MARKERS):
        return "synthetic_or_reference"
    if text.strip():
        return "attributed"
    return "unattributed"


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def neutral_default_factors(value: Any) -> list[str]:
    factors = parse_json_object(value)
    result: list[str] = []
    for key, details in factors.items():
        raw_value = details.get("value") if isinstance(details, dict) else details
        try:
            number = float(raw_value)
        except (TypeError, ValueError):
            continue
        if abs(number - 0.5) < 1e-9 or abs(number - 50.0) < 1e-9:
            result.append(str(key))
    return sorted(result)


def component_sizes(edges: Iterable[tuple[str, str]]) -> list[int]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for left, right in edges:
        adjacency[left].add(right)
        adjacency[right].add(left)
    remaining = set(adjacency)
    sizes: list[int] = []
    while remaining:
        start = remaining.pop()
        queue = deque([start])
        size = 0
        while queue:
            node = queue.popleft()
            size += 1
            for neighbor in adjacency[node]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        sizes.append(size)
    return sorted(sizes, reverse=True)


class ReadOnlyAudit:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.errors: list[dict[str, str]] = []

    def query(self, section: str, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if FORBIDDEN_CYPHER.search(cypher):
            raise ValueError(f"审计查询 {section!r} 包含写操作关键字")
        options: dict[str, Any] = {
            "default_access_mode": READ_ACCESS,
            "notifications_min_severity": "OFF",
        }
        if self.settings.database:
            options["database"] = self.settings.database
        try:
            with get_driver().session(**options) as session:
                return [to_jsonable(record.data()) for record in session.run(cypher, parameters or {})]
        except Exception as exc:
            self.errors.append({"section": section, "error_type": type(exc).__name__, "message": str(exc)[:1000]})
            return []

    def scalar(self, section: str, cypher: str, field: str) -> int:
        rows = self.query(section, cypher)
        return int(rows[0].get(field) or 0) if rows else 0

    def realtime_inventory(self) -> list[dict[str, Any]]:
        inventory: list[dict[str, Any]] = []
        for label, timestamp_fields in REALTIME_LABELS.items():
            expressions = ", ".join(f"n['{field}']" for field in timestamp_fields)
            rows = self.query(
                f"realtime_{label}",
                """
                MATCH (n)
                WHERE $label IN labels(n)
                WITH n, [value IN [{expressions}] WHERE value IS NOT NULL] AS timestamps
                UNWIND CASE WHEN timestamps = [] THEN [null] ELSE timestamps END AS timestamp
                RETURN count(DISTINCT n) AS count,
                       max(timestamp) AS latest_at,
                       collect(DISTINCT coalesce(n['provider'],n['source'],n['source_type'],n['data_source'],n['dataSource'],n['weather_source'],n['marine_source'],'unattributed'))[0..20] AS sources
                """.format(expressions=expressions),
                {"label": label},
            )
            row = rows[0] if rows else {"count": 0, "latest_at": None, "sources": []}
            inventory.append({"label": label, **row})
        return inventory

    def collect(self) -> dict[str, Any]:
        totals = {
            "nodes": self.scalar("total_nodes", "MATCH (n) RETURN count(n) AS count", "count"),
            "relationships": self.scalar("total_relationships", "MATCH ()-[r]->() RETURN count(r) AS count", "count"),
        }
        labels = self.query(
            "labels",
            "MATCH (n) UNWIND labels(n) AS label RETURN label,count(*) AS count ORDER BY count DESC,label",
        )
        relationship_types = self.query(
            "relationship_types",
            "MATCH ()-[r]->() RETURN type(r) AS type,count(*) AS count ORDER BY count DESC,type",
        )
        key_entities = self.query(
            "key_entities",
            """
            MATCH (n)
            UNWIND [label IN labels(n) WHERE label IN $labels] AS label
            RETURN label,count(*) AS count ORDER BY label
            """,
            {"labels": KEY_ENTITY_LABELS},
        )
        constraints = self.query(
            "constraints",
            "SHOW CONSTRAINTS YIELD name,type,entityType,labelsOrTypes,properties RETURN name,type,entityType,labelsOrTypes,properties ORDER BY name",
        )
        indexes = self.query(
            "indexes",
            "SHOW INDEXES YIELD name,type,entityType,labelsOrTypes,properties,state RETURN name,type,entityType,labelsOrTypes,properties,state ORDER BY name",
        )
        location_labels = sorted(LOCATION_LABELS)
        location_summary = self.query(
            "location_summary",
            """
            MATCH (n)
            WHERE any(label IN labels(n) WHERE label IN $labels)
            WITH DISTINCT n,
                 n.latitude IS NOT NULL AND n.longitude IS NOT NULL
                   AND NOT (toFloat(n.latitude)=0 AND toFloat(n.longitude)=0) AS has_coordinates,
                 coalesce(n.name_zh,n.name_en,n.name,n.city,n.code,n.unlocode,n.iata) IS NOT NULL AS has_name
            RETURN count(n) AS total,
                   count(CASE WHEN has_coordinates THEN 1 END) AS with_coordinates,
                   count(CASE WHEN has_name THEN 1 END) AS with_name,
                   count(CASE WHEN has_coordinates AND coalesce(n['coordinate_source'],n['source']) IS NOT NULL THEN 1 END) AS coordinates_with_source,
                   count(CASE WHEN coalesce(n['coordinate_status'],'')='estimated' THEN 1 END) AS estimated_coordinates
            """,
            {"labels": location_labels},
        )
        location_by_label = self.query(
            "location_by_label",
            """
            MATCH (n)
            UNWIND [label IN labels(n) WHERE label IN $labels] AS label
            RETURN label,count(*) AS total,
                   count(CASE WHEN n.latitude IS NOT NULL AND n.longitude IS NOT NULL
                     AND NOT (toFloat(n.latitude)=0 AND toFloat(n.longitude)=0) THEN 1 END) AS with_coordinates,
                   count(CASE WHEN coalesce(n.name_zh,n.name_en,n.name,n.city,n.code,n.unlocode,n.iata) IS NOT NULL THEN 1 END) AS with_name
            ORDER BY label
            """,
            {"labels": location_labels},
        )
        segment_rows = self.query(
            "route_segment_details",
            """
            MATCH (segment:RouteSegment)
            OPTIONAL MATCH (segment)-[:FROM_NODE]->(from_node)
            WITH segment,collect(DISTINCT from_node) AS from_nodes
            OPTIONAL MATCH (segment)-[:TO_NODE]->(to_node)
            RETURN elementId(segment) AS element_id,
                   coalesce(segment.segment_id,segment.segmentId,elementId(segment)) AS segment_id,
                   coalesce(segment.mode,segment.routeMode) AS mode,
                   segment['data_status'] AS data_status,
                   segment['provider'] AS provider,
                   segment.source AS source,
                   segment['source_type'] AS source_type,
                   segment['data_source'] AS data_source,
                   segment.dataSource AS dataSource,
                   segment.modelSource AS modelSource,
                   segment.source_repo AS source_repo,
                   segment['source_url'] AS source_url,
                   segment['collected_at'] AS collected_at,
                   segment['updated_at'] AS updated_at,
                   segment['confidence'] AS confidence,
                   segment.confidence_score AS confidence_score,
                   coalesce(segment.distance_km,segment.distanceKm) AS distance_km,
                   coalesce(segment.estimated_time_days,segment.estimatedTimeHours / 24.0) AS duration_days,
                   coalesce(segment.estimated_cost_usd,segment.baseCostUSD,segment.totalCostUSD) AS cost_usd,
                   coalesce(segment.total_risk_score,segment.riskScore,segment.base_risk_score) AS base_risk_score,
                   segment.dynamic_risk_score AS dynamic_risk_score,
                   segment.news_risk_score AS news_risk_score,
                   segment.news_risk_expires_at AS news_risk_expires_at,
                   segment.route_weather_risk AS route_weather_risk,
                   segment.route_weather_updated_at AS route_weather_updated_at,
                   segment.risk_breakdown AS risk_breakdown,
                   segment['geometry_geojson'] AS geometry_geojson,
                   segment['geometry_json'] AS geometry_json,
                   size(from_nodes) AS from_count,
                   count(DISTINCT to_node) AS to_count,
                   head(from_nodes) IS NOT NULL AND head(from_nodes).latitude IS NOT NULL AND head(from_nodes).longitude IS NOT NULL AS from_has_coordinates,
                   head(collect(DISTINCT to_node)) IS NOT NULL AND head(collect(DISTINCT to_node)).latitude IS NOT NULL AND head(collect(DISTINCT to_node)).longitude IS NOT NULL AS to_has_coordinates
            """,
        )
        segment_summary = summarize_segments(segment_rows)
        source_distribution = self.query(
            "source_distribution",
            """
            MATCH (n)
            UNWIND labels(n) AS label
            WITH label,
                 coalesce(toString(n['provider']),toString(n['source']),toString(n['source_type']),toString(n['data_source']),
                          toString(n['dataSource']),toString(n['modelSource']),toString(n['source_repo']),
                          toString(n['weather_source']),toString(n['marine_source']),'unattributed') AS attribution
            RETURN label,attribution,count(*) AS count
            ORDER BY label,count DESC,attribution
            """,
        )
        duplicates = {
            "location_identifiers": self.query(
                "duplicate_location_identifiers",
                """
                MATCH (n)
                WHERE any(label IN labels(n) WHERE label IN $labels)
                WITH toLower(toString(coalesce(n.location_id,n.unlocode,n.code,n.iata,n.icao,n.id,''))) AS identifier,
                     collect({element_id:elementId(n),labels:labels(n),name:coalesce(n.name_zh,n.name_en,n.name,n.city)}) AS nodes
                WHERE identifier <> '' AND size(nodes) > 1
                RETURN identifier,size(nodes) AS count,nodes[0..20] AS nodes ORDER BY count DESC,identifier LIMIT 200
                """,
                {"labels": location_labels},
            ),
            "route_segments": self.query(
                "duplicate_route_segments",
                """
                MATCH (n:RouteSegment)
                WITH toString(coalesce(n.segment_id,n.segmentId,'')) AS identifier,collect(elementId(n)) AS elements
                WHERE identifier <> '' AND size(elements) > 1
                RETURN identifier,size(elements) AS count,elements[0..20] AS element_ids ORDER BY count DESC,identifier LIMIT 200
                """,
            ),
            "routes": self.query(
                "duplicate_routes",
                """
                MATCH (n)
                WHERE n:Route OR n:VehicleRoute
                WITH toString(coalesce(n.route_id,n.routeId,'')) AS identifier,collect({element_id:elementId(n),labels:labels(n)}) AS nodes
                WHERE identifier <> '' AND size(nodes) > 1
                RETURN identifier,size(nodes) AS count,nodes[0..20] AS nodes ORDER BY count DESC,identifier LIMIT 200
                """,
            ),
        }
        orphan_summary = self.query(
            "orphan_summary",
            """
            MATCH (n)
            WHERE NOT (n)--()
            UNWIND labels(n) AS label
            RETURN label,count(*) AS count,collect(elementId(n))[0..20] AS sample_element_ids
            ORDER BY count DESC,label
            """,
        )
        edges = [
            (str(row["from_id"]), str(row["to_id"]))
            for row in self.query(
                "route_graph_edges",
                """
                MATCH (segment:RouteSegment)-[:FROM_NODE]->(from_node)
                MATCH (segment)-[:TO_NODE]->(to_node)
                RETURN elementId(from_node) AS from_id,elementId(to_node) AS to_id
                """,
            )
        ]
        components = component_sizes(edges)
        supplier_origin_summary = self.query(
            "supplier_origin_summary",
            """
            MATCH (supplier:Supplier)
            OPTIONAL MATCH (supplier)-[:SHIPS_FROM]->(ship_from)
            WITH supplier,collect(DISTINCT ship_from) AS ship_from_nodes
            OPTIONAL MATCH (segment:RouteSegment)-[:FROM_NODE]->(supplier)
            WITH supplier,ship_from_nodes,count(DISTINCT segment) AS direct_start_segments
            OPTIONAL MATCH (supplier)-[:SHIPS_FROM]->(facility)-[:HAS_ACCESS_LEG]->(access_node)
            RETURN count(DISTINCT supplier) AS suppliers,
                   count(DISTINCT CASE WHEN ship_from_nodes <> [] THEN supplier END) AS with_ships_from,
                   count(DISTINCT CASE WHEN direct_start_segments > 0 THEN supplier END) AS used_directly_as_route_origin,
                   count(DISTINCT CASE WHEN access_node IS NOT NULL THEN supplier END) AS with_required_access_chain,
                   reduce(total=0,nodes IN collect(ship_from_nodes) | total + size(nodes)) AS ships_from_relationship_targets
            """,
        )
        supplier_target_labels = self.query(
            "supplier_origin_target_labels",
            """
            MATCH (:Supplier)-[:SHIPS_FROM]->(target)
            UNWIND labels(target) AS label
            RETURN label,count(*) AS count ORDER BY count DESC,label
            """,
        )
        realtime = self.realtime_inventory()
        preservation = {
            row["label"]: {"count": row["count"], "latest_at": row["latest_at"], "sources": row["sources"]}
            for row in realtime
            if row["label"] in {"NewsRiskEvent", "NewsRiskZone", "WeatherRiskSnapshot", "VesselObservation", "PortTrafficSnapshot"}
        }
        inventory = {
            "metadata": {
                "audit_version": AUDIT_VERSION,
                "generated_at_utc": utc_now(),
                "database": self.settings.database or "default",
                "read_only": True,
                "credentials_redacted": True,
            },
            "totals": totals,
            "labels": labels,
            "relationship_types": relationship_types,
            "key_entities": key_entities,
            "schema": {"constraint_count": len(constraints), "index_count": len(indexes), "constraints": constraints, "indexes": indexes},
            "locations": {
                "summary": location_summary[0] if location_summary else {},
                "by_label": location_by_label,
            },
            "route_segments": segment_summary,
            "source_distribution": source_distribution,
            "realtime_data": realtime,
            "protected_realtime_baseline": preservation,
            "duplicates": duplicates,
            "orphans": orphan_summary,
            "connectivity": {
                "directed_edges": len(edges),
                "weak_component_count": len(components),
                "weak_component_sizes": components,
                "largest_component_nodes": components[0] if components else 0,
            },
            "supplier_origins": {
                "summary": supplier_origin_summary[0] if supplier_origin_summary else {},
                "ships_from_target_labels": supplier_target_labels,
            },
            "api_routes": collect_api_inventory(),
            "project_providers": collect_project_provider_inventory(),
            "query_errors": self.errors,
        }
        inventory["findings"] = build_findings(inventory)
        return inventory


def summarize_segments(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mode_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    data_status_counts: Counter[str] = Counter()
    source_quality_counts: Counter[str] = Counter()
    suspicious_neutral_segments: list[dict[str, Any]] = []
    totals = Counter()
    disconnected: list[dict[str, Any]] = []
    invalid_modes: list[dict[str, Any]] = []
    canonical_modes = {"road", "rail", "sea", "air"}
    now = datetime.now(timezone.utc)
    for row in rows:
        mode = str(row.get("mode") or "unavailable").casefold()
        mode_counts[mode] += 1
        source_values = [row.get(field) for field in SOURCE_FIELDS]
        attribution = next((str(value) for value in source_values if value not in (None, "")), "unattributed")
        source_counts[attribution] += 1
        source_quality_counts[source_status(*source_values)] += 1
        data_status_counts[str(row.get("data_status") or "unclassified")] += 1
        if row.get("distance_km") is not None:
            totals["with_distance"] += 1
        if row.get("duration_days") is not None:
            totals["with_duration"] += 1
        if row.get("cost_usd") is not None:
            totals["with_cost"] += 1
        if row.get("base_risk_score") is not None:
            totals["with_base_risk"] += 1
        if row.get("dynamic_risk_score") is not None:
            totals["with_dynamic_risk"] += 1
        if row.get("news_risk_score") is not None:
            totals["with_news_risk"] += 1
        if is_future_timestamp(row.get("news_risk_expires_at"), now):
            totals["with_active_news_risk"] += 1
        if row.get("route_weather_risk") is not None:
            totals["with_weather_risk"] += 1
        if row.get("risk_breakdown") not in (None, "", {}):
            totals["with_risk_breakdown"] += 1
        if row.get("geometry_geojson") not in (None, "") or row.get("geometry_json") not in (None, ""):
            totals["with_geometry"] += 1
        if row.get("provider") not in (None, ""):
            totals["with_provider"] += 1
        if row.get("source_url") not in (None, ""):
            totals["with_source_url"] += 1
        if row.get("collected_at") is not None or row.get("updated_at") is not None:
            totals["with_timestamp"] += 1
        if row.get("confidence") is not None or row.get("confidence_score") is not None:
            totals["with_confidence"] += 1
        if row.get("from_has_coordinates") and row.get("to_has_coordinates"):
            totals["with_endpoint_coordinates"] += 1
        neutral = neutral_default_factors(row.get("risk_breakdown"))
        if neutral:
            totals["with_neutral_05_factors"] += 1
            totals["neutral_05_factor_count"] += len(neutral)
            if len(suspicious_neutral_segments) < 50:
                suspicious_neutral_segments.append({"segment_id": row.get("segment_id"), "factors": neutral})
        if row.get("from_count") != 1 or row.get("to_count") != 1:
            disconnected.append(
                {"segment_id": row.get("segment_id"), "from_count": row.get("from_count"), "to_count": row.get("to_count")}
            )
        if mode not in canonical_modes:
            invalid_modes.append({"segment_id": row.get("segment_id"), "mode": mode})
    return {
        "total": len(rows),
        "mode_distribution": counter_rows(mode_counts, "mode"),
        "source_distribution": counter_rows(source_counts, "source"),
        "source_quality": counter_rows(source_quality_counts, "status"),
        "data_status_distribution": counter_rows(data_status_counts, "data_status"),
        "field_completeness": dict(sorted(totals.items())),
        "disconnected_or_ambiguous_count": len(disconnected),
        "disconnected_or_ambiguous_samples": disconnected[:100],
        "noncanonical_mode_count": len(invalid_modes),
        "noncanonical_mode_distribution": counter_rows(Counter(item["mode"] for item in invalid_modes), "mode"),
        "noncanonical_mode_samples": invalid_modes[:100],
        "neutral_default_risk_samples": suspicious_neutral_segments,
    }


def is_future_timestamp(value: Any, now: datetime) -> bool:
    if value is None:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed > now


def counter_rows(counter: Counter[str], key_name: str) -> list[dict[str, Any]]:
    return [{key_name: key, "count": count} for key, count in counter.most_common()]


def collect_api_inventory() -> list[dict[str, Any]]:
    from app.main import app

    routes: list[dict[str, Any]] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = sorted(getattr(route, "methods", set()) or set())
        if not path or not methods:
            continue
        routes.append(
            {
                "path": path,
                "methods": methods,
                "name": getattr(route, "name", None),
                "deprecated": bool(getattr(route, "deprecated", False)),
                "tags": list(getattr(route, "tags", None) or []),
            }
        )
    return sorted(routes, key=lambda item: (item["path"], item["methods"]))


def collect_project_provider_inventory() -> list[dict[str, Any]]:
    root = Path(__file__).resolve().parents[1]
    ais_files = sorted(
        str(path.relative_to(root))
        for pattern in ("**/*ais*.py", "**/*ais*.json")
        for path in root.glob(pattern)
        if ".venv" not in path.parts and "artifacts" not in path.parts
    )
    providers = [
        {
            "provider": "GDELT DOC API",
            "implementation_status": "implemented",
            "files": ["gdelt/client.py", "gdelt/service.py", ".github/workflows/update-gdelt-risk.yml"],
            "credential_configured": None,
        },
        {
            "provider": "Open-Meteo Forecast / Marine API",
            "implementation_status": "implemented",
            "files": ["weather/client.py", "weather/service.py"],
            "credential_configured": None,
        },
        {
            "provider": "AISStream.io",
            "implementation_status": "implemented" if ais_files else "missing",
            "files": ais_files,
            "credential_configured": bool(os.getenv("AISSTREAM_API_KEY")),
        },
    ]
    for name, environment_name in (
        ("MarineTraffic", "MARINETRAFFIC_API_KEY"),
        ("OpenSky", "OPENSKY_USERNAME"),
        ("Aviation Edge", "AVIATION_EDGE_API_KEY"),
        ("FlightAware", "FLIGHTAWARE_API_KEY"),
        ("Cirium", "CIRIUM_APP_ID"),
    ):
        providers.append(
            {
                "provider": name,
                "implementation_status": "disabled_stub",
                "files": ["app/vehicle_network/providers/stubs.py"],
                "credential_configured": bool(os.getenv(environment_name)),
            }
        )
    return providers


def build_findings(inventory: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    segments = inventory["route_segments"]
    total = int(segments.get("total") or 0)
    completeness = segments.get("field_completeness", {})
    locations = inventory["locations"].get("summary", {})
    realtime = {row["label"]: row for row in inventory["realtime_data"]}
    if segments.get("noncanonical_mode_count"):
        findings.append(
            {"severity": "high", "code": "NONCANONICAL_ROUTE_MODES", "message": f"{segments['noncanonical_mode_count']} 个分段使用非 road/rail/sea/air 模式。"}
        )
    if segments.get("disconnected_or_ambiguous_count"):
        findings.append(
            {"severity": "critical", "code": "BROKEN_SEGMENT_ENDPOINTS", "message": f"{segments['disconnected_or_ambiguous_count']} 个分段缺少或重复 FROM_NODE/TO_NODE。"}
        )
    neutral_count = int(completeness.get("with_neutral_05_factors") or 0)
    if neutral_count:
        findings.append(
            {"severity": "high", "code": "NEUTRAL_DEFAULT_RISK", "message": f"{neutral_count} 个分段风险明细包含 0.5/50 默认值，需要在后续迁移中核验 Provider。"}
        )
    provider_count = int(completeness.get("with_provider") or 0)
    if total and provider_count < total:
        findings.append(
            {"severity": "high", "code": "MISSING_SEGMENT_PROVIDER", "message": f"仅 {provider_count}/{total} 个分段具有 provider 字段。"}
        )
    weather_count = int(completeness.get("with_weather_risk") or 0)
    if total and weather_count < total:
        findings.append(
            {"severity": "medium", "code": "LOW_ROUTE_WEATHER_COVERAGE", "message": f"仅 {weather_count}/{total} 个分段具有 route_weather_risk。"}
        )
    geometry_count = int(completeness.get("with_geometry") or 0)
    if total and geometry_count < total:
        findings.append(
            {"severity": "high", "code": "MISSING_ROUTE_GEOMETRY", "message": f"仅 {geometry_count}/{total} 个分段具有可审计的路线 geometry。"}
        )
    synthetic_count = next(
        (int(row["count"]) for row in segments.get("source_quality", []) if row.get("status") == "synthetic_or_reference"),
        0,
    )
    if synthetic_count:
        findings.append(
            {"severity": "high", "code": "REFERENCE_ROUTE_DATA", "message": f"{synthetic_count}/{total} 个分段来源属于合成、骨架或外部参考字段，不能视为已验证运输服务。"}
        )
    location_total = int(locations.get("total") or 0)
    coordinates = int(locations.get("with_coordinates") or 0)
    if location_total and coordinates < location_total:
        findings.append(
            {"severity": "high", "code": "MISSING_LOCATION_COORDINATES", "message": f"地点坐标覆盖率为 {coordinates}/{location_total}。"}
        )
    if int(realtime.get("VesselObservation", {}).get("count") or 0) <= 1:
        findings.append(
            {"severity": "high", "code": "AIS_NOT_OPERATIONAL", "message": "AIS 船位观测不足，不能据此计算实时港口拥堵。"}
        )
    supplier_origins = inventory.get("supplier_origins", {}).get("summary", {})
    if int(supplier_origins.get("with_required_access_chain") or 0) < int(supplier_origins.get("suppliers") or 0):
        findings.append(
            {
                "severity": "high",
                "code": "SUPPLIER_ORIGIN_CHAIN_MISSING",
                "message": (
                    f"仅 {int(supplier_origins.get('with_required_access_chain') or 0)}/"
                    f"{int(supplier_origins.get('suppliers') or 0)} 个供应商具有 SHIPS_FROM→设施→HAS_ACCESS_LEG 链。"
                ),
            }
        )
    if inventory.get("query_errors"):
        findings.append(
            {"severity": "medium", "code": "AUDIT_QUERY_ERRORS", "message": f"{len(inventory['query_errors'])} 个可选审计查询失败，请查看 query_errors。"}
        )
    return findings


def percentage(value: Any, total: Any) -> str:
    denominator = int(total or 0)
    return "0.0%" if denominator == 0 else f"{int(value or 0) / denominator:.1%}"


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_无记录_\n"
    result = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        values = [str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ") for value in row]
        result.append("| " + " | ".join(values) + " |")
    return "\n".join(result) + "\n"


def render_report(inventory: dict[str, Any]) -> str:
    metadata = inventory["metadata"]
    totals = inventory["totals"]
    locations = inventory["locations"].get("summary", {})
    segments = inventory["route_segments"]
    fields = segments.get("field_completeness", {})
    total_segments = int(segments.get("total") or 0)
    lines = [
        "# 当前后端与 AuraDB 只读审计报告",
        "",
        f"> 生成时间（UTC）：`{metadata['generated_at_utc']}`  ",
        f"> 审计版本：`{metadata['audit_version']}`  ",
        f"> 数据库：`{metadata['database']}`  ",
        "> 本报告由只读 Neo4j 会话生成；本阶段未执行新增、修改或删除。",
        "",
        "## 1. 审计结论",
        "",
    ]
    if inventory["findings"]:
        severity_name = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}
        for finding in inventory["findings"]:
            lines.append(f"- **{severity_name.get(finding['severity'], finding['severity'])}** `{finding['code']}`：{finding['message']}")
    else:
        lines.append("- 本次自动审计没有发现已定义的数据质量告警。")
    lines.extend(
        [
            "",
            "## 2. 数据库总览",
            "",
            markdown_table(
                ["指标", "数量"],
                [
                    ["节点", totals["nodes"]],
                    ["关系", totals["relationships"]],
                    ["节点标签种类", len(inventory["labels"])],
                    ["关系类型种类", len(inventory["relationship_types"])],
                    ["约束", inventory["schema"]["constraint_count"]],
                    ["索引", inventory["schema"]["index_count"]],
                ],
            ),
            "### 2.1 主要节点标签",
            "",
            markdown_table(["标签", "数量"], [[row["label"], row["count"]] for row in inventory["labels"][:50]]),
            "### 2.2 主要关系类型",
            "",
            markdown_table(["关系类型", "数量"], [[row["type"], row["count"]] for row in inventory["relationship_types"][:50]]),
            "## 3. 关键业务实体",
            "",
            markdown_table(["标签", "数量"], [[row["label"], row["count"]] for row in inventory["key_entities"]]),
            "### 3.1 供应商发货起点关系",
            "",
            markdown_table(
                ["供应商", "有 SHIPS_FROM", "直接作为分段起点", "具有 SHIPS_FROM→设施→HAS_ACCESS_LEG", "SHIPS_FROM 目标数"],
                [[
                    inventory["supplier_origins"]["summary"].get("suppliers", 0),
                    inventory["supplier_origins"]["summary"].get("with_ships_from", 0),
                    inventory["supplier_origins"]["summary"].get("used_directly_as_route_origin", 0),
                    inventory["supplier_origins"]["summary"].get("with_required_access_chain", 0),
                    inventory["supplier_origins"]["summary"].get("ships_from_relationship_targets", 0),
                ]],
            ),
            markdown_table(
                ["SHIPS_FROM 目标标签", "数量"],
                [[row["label"], row["count"]] for row in inventory["supplier_origins"]["ships_from_target_labels"]],
            ),
            "## 4. 地点与坐标完整度",
            "",
            markdown_table(
                ["地点总数", "有名称", "有坐标", "坐标有来源", "标记为估算", "坐标覆盖率"],
                [[
                    locations.get("total", 0),
                    locations.get("with_name", 0),
                    locations.get("with_coordinates", 0),
                    locations.get("coordinates_with_source", 0),
                    locations.get("estimated_coordinates", 0),
                    percentage(locations.get("with_coordinates"), locations.get("total")),
                ]],
            ),
            markdown_table(
                ["标签", "数量", "有名称", "有坐标", "坐标覆盖率"],
                [[row["label"], row["total"], row["with_name"], row["with_coordinates"], percentage(row["with_coordinates"], row["total"])] for row in inventory["locations"]["by_label"]],
            ),
            "## 5. RouteSegment 审计",
            "",
            markdown_table(
                ["指标", "数量", "覆盖率"],
                [[name, fields.get(key, 0), percentage(fields.get(key), total_segments)] for name, key in [
                    ("分段总数", "__total__"),
                    ("距离", "with_distance"),
                    ("时效", "with_duration"),
                    ("成本", "with_cost"),
                    ("基础风险", "with_base_risk"),
                    ("动态风险", "with_dynamic_risk"),
                    ("新闻风险", "with_news_risk"),
                    ("有效期内新闻风险", "with_active_news_risk"),
                    ("天气风险", "with_weather_risk"),
                    ("风险明细", "with_risk_breakdown"),
                    ("路线 geometry", "with_geometry"),
                    ("起终点均有坐标", "with_endpoint_coordinates"),
                    ("Provider", "with_provider"),
                    ("来源 URL", "with_source_url"),
                    ("时间字段", "with_timestamp"),
                    ("置信度", "with_confidence"),
                    ("包含 0.5/50 默认风险", "with_neutral_05_factors"),
                ]],
            ).replace("| 分段总数 | 0 | 0.0% |", f"| 分段总数 | {total_segments} | 100.0% |"),
            "### 5.1 运输方式",
            "",
            markdown_table(["mode", "数量"], [[row["mode"], row["count"]] for row in segments["mode_distribution"]]),
            "### 5.2 分段来源",
            "",
            markdown_table(["来源", "数量"], [[row["source"], row["count"]] for row in segments["source_distribution"]]),
            "### 5.3 数据来源质量分类",
            "",
            markdown_table(["分类", "数量"], [[row["status"], row["count"]] for row in segments["source_quality"]]),
            "### 5.4 结构异常",
            "",
            f"- 缺少或重复 `FROM_NODE/TO_NODE` 的分段：**{segments['disconnected_or_ambiguous_count']}**。",
            f"- 使用非标准 mode 的分段：**{segments['noncanonical_mode_count']}**。",
            f"- 包含可疑 0.5/50 默认风险的分段：**{fields.get('with_neutral_05_factors', 0)}**。",
            "",
            "## 6. 实时与观测数据基线",
            "",
            "> 这些记录是后续清理阶段必须优先保护的数据基线。数量为 0 不代表 Provider 不可接入，只表示当前 AuraDB 没有相应记录。",
            "",
            markdown_table(
                ["标签", "数量", "最近时间", "来源"],
                [[row["label"], row["count"], row.get("latest_at"), ", ".join(str(item) for item in row.get("sources", []))] for row in inventory["realtime_data"]],
            ),
            "### 6.1 项目 Provider 实现状态",
            "",
            markdown_table(
                ["Provider", "代码状态", "凭证是否配置", "相关文件"],
                [[
                    row["provider"],
                    row["implementation_status"],
                    "不需要" if row["credential_configured"] is None else "是" if row["credential_configured"] else "否",
                    ", ".join(row["files"]) or "—",
                ] for row in inventory["project_providers"]],
            ),
            "### 6.2 当前 FastAPI 路由",
            "",
            f"当前应用共注册 **{len(inventory['api_routes'])}** 个 HTTP 路由（包含 `/docs` 等框架路由）。",
            "",
            markdown_table(
                ["方法", "路径", "标签", "deprecated"],
                [[", ".join(row["methods"]), row["path"], ", ".join(row["tags"]), "是" if row["deprecated"] else "否"] for row in inventory["api_routes"]],
            ),
            "## 7. 重复、孤立与连通性",
            "",
            markdown_table(
                ["问题", "分组数量"],
                [
                    ["重复地点标识", len(inventory["duplicates"]["location_identifiers"])],
                    ["重复 RouteSegment 标识", len(inventory["duplicates"]["route_segments"])],
                    ["重复 Route/VehicleRoute 标识", len(inventory["duplicates"]["routes"])],
                    ["存在孤立节点的标签", len(inventory["orphans"])],
                    ["路线图弱连通分量", inventory["connectivity"]["weak_component_count"]],
                    ["最大弱连通分量节点数", inventory["connectivity"]["largest_component_nodes"]],
                ],
            ),
            "### 7.1 孤立节点",
            "",
            markdown_table(["标签", "数量", "示例 elementId"], [[row["label"], row["count"], ", ".join(row["sample_element_ids"][:5])] for row in inventory["orphans"]]),
            "## 8. 阶段 2 清理设计建议（未执行）",
            "",
            "1. 以本报告的 `protected_realtime_baseline` 为保护清单，任何清理规则必须先排除实时 API 与有明确来源的数据。",
            "2. 优先将 `fabricated_for_testing`、`synthetic`、`sample`、`mock` 和 `standard_skeleton_reference` 标记为候选，不要仅凭字段为空删除。",
            "3. 对 `external_repos_reference_fields` 和 `Tesla SEC route skeleton` 先判定业务用途，再决定保留为 `estimated/reference` 或删除。",
            "4. 实际清理前必须导出候选节点、关系、属性和删除原因，并执行 dry-run。",
            "5. 无 Provider 的 0.5/50 风险应在风险迁移阶段改为 unavailable，而不是把整条路线直接删除。",
            "",
            "## 9. 查询错误",
            "",
        ]
    )
    if inventory["query_errors"]:
        lines.append(markdown_table(["区段", "错误类型", "信息"], [[row["section"], row["error_type"], row["message"]] for row in inventory["query_errors"]]))
    else:
        lines.append("本次审计查询全部成功。\n")
    lines.extend(
        [
            "## 10. 复现命令",
            "",
            "```bash",
            "python scripts/audit_current_backend.py",
            "```",
            "",
            "机器可读库存：`artifacts/database_inventory.json`。",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(inventory: dict[str, Any], json_path: Path, report_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_report(inventory), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="只读审计 FastAPI 后端所连接的 Neo4j/AuraDB 数据结构与质量")
    parser.add_argument("--json", type=Path, default=Path("artifacts/database_inventory.json"), help="机器可读库存输出路径")
    parser.add_argument("--report", type=Path, default=Path("docs/current_backend_audit.md"), help="中文审计报告输出路径")
    args = parser.parse_args()
    audit = ReadOnlyAudit()
    try:
        get_driver().verify_connectivity()
        inventory = audit.collect()
        write_outputs(inventory, args.json, args.report)
    finally:
        close_driver()
    print(
        json.dumps(
            {
                "read_only": True,
                "nodes": inventory["totals"]["nodes"],
                "relationships": inventory["totals"]["relationships"],
                "route_segments": inventory["route_segments"]["total"],
                "findings": len(inventory["findings"]),
                "query_errors": len(inventory["query_errors"]),
                "json": str(args.json),
                "report": str(args.report),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
