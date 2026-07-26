from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neo4j import READ_ACCESS, WRITE_ACCESS

from app.provider_risk import (
    PROVIDER_RISK_VERSION,
    build_segment_signals,
    calculate_provider_risk,
    database_risk_properties,
    is_fresh,
    parse_datetime,
)
from app.vehicle_network.core import load_strategy
from database.neo4j_client import close_driver, get_driver, get_settings, to_jsonable
from scripts.cleanup_synthetic_data import redact_sensitive_properties


EXECUTE_CONFIRMATION = "CLEAN_AND_RECALCULATE_PROVIDER_RISK_V1"
RISK_FACTOR_SOURCE_MARKERS = (
    "derived_from_route_segment",
    "port_risk_standardization",
    "country_risk_standardization",
    "synthetic_cross_border_transport_risk",
    "synthetic_experiment",
    "synthetic",
    "fabricated",
    "sample",
    "mock",
)
PROTECTED_REVIEW_STATUSES = {"approved", "verified", "reviewed"}
PROTECTED_REALTIME_LABELS = {
    "NewsRiskEvent",
    "NewsRiskZone",
    "WeatherRiskSnapshot",
    "Vessel",
    "VesselObservation",
    "PortTrafficSnapshot",
    "Shipment",
}
PROTECTED_PROVIDER_MARKERS = {
    "gdelt",
    "open-meteo",
    "open meteo",
    "aisstream",
    "official_registry",
    "official_schedule",
    "paid_api",
    "open_api",
}
SEGMENT_FORBIDDEN_RISK_FIELDS = (
    "riskScore",
    "base_risk_score",
    "costRiskScore",
    "supplier_risk",
    "comprehensive_risk_score",
)
CLEARABLE_RISK_FIELDS = (
    "riskScore",
    "base_risk_score",
    "costRiskScore",
    "supplier_risk",
    "total_risk_score",
    "dynamic_risk_score",
    "risk_score",
    "comprehensive_risk_score",
    "risk_level",
    "riskLevel",
    "risk_breakdown",
    "risk_explanation",
    "risk_factors",
    "riskFactors",
    "risk_factors_json",
)
CLEARABLE_LABELS = (
    "Supplier",
    "Factory",
    "Warehouse",
    "Port",
    "Airport",
    "RailTerminal",
    "Route",
    "VehicleRoute",
    "RiskSnapshot",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def source_text(properties: dict[str, Any]) -> str:
    fields = (
        "provider",
        "source",
        "source_type",
        "source_url",
        "data_source",
        "dataSource",
        "modelSource",
        "weather_source",
        "marine_source",
    )
    return " ".join(str(properties.get(field) or "") for field in fields).casefold()


def risk_factor_selection_reasons(node: dict[str, Any]) -> list[str]:
    properties = node.get("properties") or {}
    labels = set(node.get("labels") or [])
    if "RiskFactor" not in labels:
        return []
    if str(properties.get("review_status") or "").casefold() in PROTECTED_REVIEW_STATUSES:
        return []
    if properties.get("provider") or properties.get("source_url"):
        return []
    if int(node.get("evidence_count") or 0) > 0:
        return []
    text = source_text(properties)
    return [f"unverified_source:{marker}" for marker in RISK_FACTOR_SOURCE_MARKERS if marker in text]


def protected_neighbor_reasons(relationship: dict[str, Any], candidate_id: str) -> list[str]:
    if str(relationship["start_element_id"]) == candidate_id:
        labels = set(relationship.get("end_labels") or [])
        properties = relationship.get("end_properties") or {}
    else:
        labels = set(relationship.get("start_labels") or [])
        properties = relationship.get("start_properties") or {}
    reasons = [f"protected_label:{label}" for label in sorted(labels & PROTECTED_REALTIME_LABELS)]
    provider_source = source_text(properties)
    if "RiskObservation" in labels and properties.get("provider"):
        reasons.append("provider_risk_observation")
    if "Evidence" in labels and (properties.get("provider") or properties.get("source_url")):
        reasons.append("provider_evidence")
    if labels & (PROTECTED_REALTIME_LABELS | {"RiskObservation", "Evidence"}):
        reasons.extend(
            f"protected_source:{marker}"
            for marker in sorted(PROTECTED_PROVIDER_MARKERS)
            if marker in provider_source
        )
    return reasons


def build_risk_factor_plan(
    nodes: list[dict[str, Any]], relationships: list[dict[str, Any]]
) -> dict[str, Any]:
    relationship_by_node: dict[str, list[dict[str, Any]]] = {}
    for relationship in relationships:
        for key in ("start_element_id", "end_element_id"):
            relationship_by_node.setdefault(str(relationship[key]), []).append(relationship)
    dispositions: dict[str, dict[str, Any]] = {}
    deletion_ids: list[str] = []
    for node in nodes:
        element_id = str(node["element_id"])
        selection_reasons = risk_factor_selection_reasons(node)
        if not selection_reasons:
            dispositions[element_id] = {"status": "retained", "reasons": ["not_an_unverified_synthetic_factor"]}
            continue
        blocking: set[str] = set()
        for relationship in relationship_by_node.get(element_id, []):
            blocking.update(protected_neighbor_reasons(relationship, element_id))
        if blocking:
            dispositions[element_id] = {"status": "blocked", "reasons": sorted(blocking)}
        else:
            dispositions[element_id] = {"status": "candidate", "reasons": selection_reasons}
            deletion_ids.append(element_id)
    return {
        "deletion_ids": sorted(deletion_ids),
        "dispositions": dispositions,
        "counts": dict(Counter(item["status"] for item in dispositions.values())),
    }


def active_news_signal(segment: dict[str, Any], now: datetime) -> dict[str, Any]:
    properties = segment.get("properties") or {}
    zones = [
        zone
        for zone in segment.get("news_zones") or []
        if zone.get("zone_id") and str(zone.get("provider") or "").casefold() == "gdelt"
    ]
    expires_at = parse_datetime(properties.get("news_risk_expires_at"))
    if not zones or expires_at is None or expires_at <= now:
        return {}
    zone_confidences = [zone.get("confidence") for zone in zones if zone.get("confidence") is not None]
    return {
        "news_score": properties.get("news_risk_score"),
        "news_provider": "GDELT",
        "news_observed_at": properties.get("news_risk_updated_at"),
        "news_expires_at": properties.get("news_risk_expires_at"),
        "news_confidence": properties.get("news_risk_confidence") if properties.get("news_risk_confidence") is not None else max(zone_confidences, default=None),
        "news_evidence": sorted({str(zone["zone_id"]) for zone in zones}),
    }


def active_weather_signal(segment: dict[str, Any], now: datetime, max_age_hours: float) -> dict[str, Any]:
    properties = segment.get("properties") or {}
    relationship_evidence = [
        item
        for item in segment.get("weather_evidence") or []
        if str(item.get("provider") or "").casefold() in {"open-meteo", "open meteo"}
    ]
    property_evidence = [str(item) for item in properties.get("route_weather_evidence") or [] if item]
    updated_at = properties.get("route_weather_updated_at")
    expires_at = parse_datetime(properties.get("route_weather_expires_at"))
    active = expires_at > now if expires_at is not None else is_fresh(updated_at, now=now, max_age_hours=max_age_hours)
    if (
        str(properties.get("route_weather_provider") or "").casefold() not in {"open-meteo", "open meteo"}
        or not active
        or not (property_evidence or relationship_evidence)
    ):
        return {}
    observed_at = parse_datetime(updated_at)
    fallback_expiry = observed_at.timestamp() + max_age_hours * 3600 if observed_at is not None else None
    expires_text = (
        expires_at.isoformat()
        if expires_at is not None
        else datetime.fromtimestamp(fallback_expiry, tz=timezone.utc).isoformat()
        if fallback_expiry
        else None
    )
    return {
        "weather_score": properties.get("route_weather_risk"),
        "weather_provider": "Open-Meteo",
        "weather_observed_at": updated_at,
        "weather_expires_at": expires_text,
        "weather_confidence": properties.get("route_weather_confidence"),
        "weather_evidence": sorted(
            set(property_evidence)
            | {
                str(item.get("snapshot_id") or item.get("port_id"))
                for item in relationship_evidence
                if item.get("snapshot_id") or item.get("port_id")
            }
        ),
    }


def build_segment_recalculation(
    segment: dict[str, Any],
    *,
    now: datetime,
    max_weather_age_hours: float,
    strategy: Any,
) -> dict[str, Any] | None:
    properties = segment.get("properties") or {}
    mode = properties.get("canonical_mode") or properties.get("mode") or properties.get("routeMode")
    signal_arguments = {
        **active_news_signal(segment, now),
        **active_weather_signal(segment, now, max_weather_age_hours),
    }
    signals = build_segment_signals(mode, **signal_arguments)
    result = calculate_provider_risk(mode, signals, strategy)
    new_properties = database_risk_properties(result, now)
    has_forbidden_fields = any(properties.get(field) is not None for field in SEGMENT_FORBIDDEN_RISK_FIELDS)
    if (
        properties.get("risk_input_hash") == new_properties["risk_input_hash"]
        and properties.get("risk_scoring_version") == PROVIDER_RISK_VERSION
        and not has_forbidden_fields
    ):
        return None
    return {
        "element_id": segment["element_id"],
        "segment_id": properties.get("segment_id") or properties.get("segmentId") or segment["element_id"],
        "mode": str(mode or "unknown").casefold(),
        "before": redact_sensitive_properties(properties),
        "properties": new_properties,
        "result": result,
        "signals": signals,
    }


def has_clearable_risk(properties: dict[str, Any]) -> bool:
    if properties.get("risk_scoring_version") == PROVIDER_RISK_VERSION:
        score_fields = (
            "riskScore",
            "base_risk_score",
            "costRiskScore",
            "supplier_risk",
            "total_risk_score",
            "dynamic_risk_score",
            "risk_score",
            "comprehensive_risk_score",
            "risk_level",
            "riskLevel",
            "risk_breakdown",
            "risk_factors",
            "riskFactors",
            "risk_factors_json",
        )
        if not any(properties.get(field) is not None for field in score_fields):
            return False
    return any(properties.get(field) is not None for field in CLEARABLE_RISK_FIELDS)


def build_clear_rows(nodes: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for node in nodes:
        properties = node.get("properties") or {}
        review_status = str(properties.get("risk_review_status") or properties.get("review_status") or "").casefold()
        if review_status in PROTECTED_REVIEW_STATUSES or properties.get("risk_provider"):
            continue
        if not has_clearable_risk(properties):
            continue
        rows.append(
            {
                "element_id": node["element_id"],
                "labels": node.get("labels") or [],
                "identity": node.get("identity") or {},
                "before": redact_sensitive_properties(
                    {field: properties.get(field) for field in CLEARABLE_RISK_FIELDS if properties.get(field) is not None}
                ),
                "properties": {
                    "provider_risk_score": None,
                    "provider_risk_score_100": None,
                    "provider_risk_level": "unknown",
                    "provider_risk_status": "unavailable",
                    "provider_risk_data_completeness": 0.0,
                    "provider_risk_confidence": None,
                    "provider_risk_missing_factors": [],
                    "provider_risk_providers": [],
                    "provider_risk_evidence": [],
                    "risk_status": "unavailable",
                    "risk_data_completeness": 0.0,
                    "risk_scoring_version": PROVIDER_RISK_VERSION,
                    "risk_recalculated_at": now.isoformat(),
                    "risk_explanation": "已移除无真实 Provider 或审核证据的旧风险值，等待可验证观测",
                },
            }
        )
    return rows


class RiskRecalculationRepository:
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
        rows = self.read("MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes,count(r) AS relationships")
        if not rows:
            return {"nodes": 0, "relationships": 0}
        return {"nodes": int(rows[0]["nodes"]), "relationships": int(rows[0]["relationships"])}

    def protected_baseline(self) -> dict[str, int]:
        rows = self.read(
            """
            MATCH (node)
            UNWIND [label IN labels(node) WHERE label IN $labels] AS label
            RETURN label,count(*) AS count ORDER BY label
            """,
            {"labels": sorted(PROTECTED_REALTIME_LABELS)},
        )
        baseline = {label: 0 for label in sorted(PROTECTED_REALTIME_LABELS)}
        baseline.update({row["label"]: int(row["count"]) for row in rows})
        return baseline

    def risk_factors(self) -> list[dict[str, Any]]:
        return self.read(
            """
            MATCH (factor:RiskFactor)
            OPTIONAL MATCH (factor)-[]-(evidence:Evidence)
            WITH factor,count(DISTINCT evidence) AS evidence_count
            RETURN elementId(factor) AS element_id,labels(factor) AS labels,
                   properties(factor) AS properties,evidence_count
            ORDER BY element_id
            """
        )

    def attached_relationships(self, element_ids: list[str], full_properties: bool = False) -> list[dict[str, Any]]:
        if not element_ids:
            return []
        property_expression = "properties(relationship)" if full_properties else "{}"
        node_expression = "properties(start)" if full_properties else "{provider:start.provider,source:start.source,source_type:start.source_type,source_url:start.source_url,review_status:start.review_status}"
        end_expression = "properties(end)" if full_properties else "{provider:end.provider,source:end.source,source_type:end.source_type,source_url:end.source_url,review_status:end.review_status}"
        return self.read(
            f"""
            MATCH (selected) WHERE elementId(selected) IN $element_ids
            MATCH (selected)-[relationship]-()
            WITH DISTINCT relationship,startNode(relationship) AS start,endNode(relationship) AS end
            RETURN elementId(relationship) AS element_id,type(relationship) AS type,
                   {property_expression} AS properties,
                   elementId(start) AS start_element_id,labels(start) AS start_labels,
                   {node_expression} AS start_properties,
                   elementId(end) AS end_element_id,labels(end) AS end_labels,
                   {end_expression} AS end_properties
            ORDER BY element_id
            """,
            {"element_ids": element_ids},
        )

    def route_segments(self) -> list[dict[str, Any]]:
        return self.read(
            """
            MATCH (segment:RouteSegment)
            OPTIONAL MATCH (segment)-[:EXPOSED_TO_NEWS_RISK]->(zone:NewsRiskZone)
            WITH segment,collect(DISTINCT {
              zone_id:zone.zone_id,provider:zone.provider,expires_at:zone.expires_at,
              confidence:zone.confidence
            }) AS raw_zones
            OPTIONAL MATCH (segment)-[:FROM_NODE|TO_NODE]->(port:Port)
            OPTIONAL MATCH (port)-[:HAS_WEATHER_SNAPSHOT]->(weather:WeatherRiskSnapshot)
            WITH segment,raw_zones,collect(DISTINCT {
              port_id:coalesce(port.unlocode,port.code,port.port_id),
              snapshot_id:weather.snapshot_id,provider:weather.provider,
              observed_at:weather.observed_at
            }) AS port_weather
            OPTIONAL MATCH (segment)-[:HAS_ROUTE_WEATHER_SNAPSHOT]->(route_weather:RouteWeatherRiskSnapshot)
            WITH segment,raw_zones,port_weather,collect(DISTINCT {
              port_id:null,snapshot_id:route_weather.snapshot_id,provider:route_weather.provider,
              observed_at:route_weather.observed_at
            }) AS route_weather_rows
            RETURN elementId(segment) AS element_id,properties(segment) AS properties,
                   [item IN raw_zones WHERE item.zone_id IS NOT NULL] AS news_zones,
                   [item IN port_weather + route_weather_rows WHERE item.provider IS NOT NULL] AS weather_evidence
            ORDER BY element_id
            """
        )

    def clearable_nodes(self) -> list[dict[str, Any]]:
        return self.read(
            """
            MATCH (node)
            WHERE any(label IN labels(node) WHERE label IN $labels)
              AND NOT node:RouteSegment
              AND any(field IN $risk_fields WHERE node[field] IS NOT NULL)
            RETURN elementId(node) AS element_id,labels(node) AS labels,properties(node) AS properties,
                   {
                     id:coalesce(node.route_id,node.snapshot_id,node.supplier_id,node.factory_id,
                                 node.unlocode,node.iata,node.terminal_code,node.location_id,node.id),
                     name:node.name
                   } AS identity
            ORDER BY element_id
            """,
            {"labels": list(CLEARABLE_LABELS), "risk_fields": list(CLEARABLE_RISK_FIELDS)},
        )

    def statistics(self) -> dict[str, Any]:
        summary_rows = self.read(
            """
            MATCH (node)
            WITH collect(node) AS nodes
            RETURN
              size([node IN nodes WHERE node:RiskFactor]) AS risk_factor_nodes,
              size([node IN nodes WHERE node:RiskFactor AND node.provider IS NULL]) AS providerless_risk_factors,
              size([node IN nodes WHERE node:RouteSegment]) AS route_segments,
              size([node IN nodes WHERE node:RouteSegment AND node.provider_risk_status='available']) AS risk_available,
              size([node IN nodes WHERE node:RouteSegment AND node.provider_risk_status='partial']) AS risk_partial,
              size([node IN nodes WHERE node:RouteSegment AND node.provider_risk_status='unavailable']) AS risk_unavailable,
              size([node IN nodes WHERE any(field IN $fields WHERE node[field] IS NOT NULL)]) AS legacy_risk_nodes
            """,
            {"fields": list(SEGMENT_FORBIDDEN_RISK_FIELDS)},
        )
        providers = self.read(
            """
            MATCH (observation:RiskObservation)
            RETURN coalesce(observation.provider,'unattributed') AS provider,count(*) AS count
            ORDER BY count DESC,provider
            """
        )
        modes = self.read(
            """
            MATCH (segment:RouteSegment)
            RETURN toLower(toString(coalesce(segment.canonical_mode,segment.mode,segment.routeMode,'unknown'))) AS mode,
                   coalesce(segment.provider_risk_status,'not_calculated') AS status,count(*) AS count
            ORDER BY mode,status
            """
        )
        return {
            "summary": summary_rows[0] if summary_rows else {},
            "risk_observations_by_provider": providers,
            "segment_coverage_by_mode": modes,
        }

    def execute(
        self,
        deletion_ids: list[str],
        segment_rows: list[dict[str, Any]],
        clear_rows: list[dict[str, Any]],
        protected_before: dict[str, int],
    ) -> dict[str, int]:
        def write(transaction):
            deleted_nodes = 0
            deleted_relationships = 0
            if deletion_ids:
                result = transaction.run(
                    """
                    UNWIND $element_ids AS element_id
                    MATCH (factor:RiskFactor) WHERE elementId(factor)=element_id
                    DETACH DELETE factor
                    RETURN count(*) AS deleted
                    """,
                    element_ids=deletion_ids,
                )
                record = result.single()
                summary = result.consume()
                deleted_nodes = int(record["deleted"] if record else 0)
                deleted_relationships = int(summary.counters.relationships_deleted)
            segment_updates = 0
            if segment_rows:
                record = transaction.run(
                    """
                    UNWIND $rows AS row
                    MATCH (segment:RouteSegment) WHERE elementId(segment)=row.element_id
                    REMOVE segment.riskScore,segment.base_risk_score,segment.costRiskScore,
                           segment.supplier_risk,segment.comprehensive_risk_score
                    SET segment += row.properties
                    SET segment.risk_recalculated_at=datetime(row.properties.risk_recalculated_at)
                    RETURN count(segment) AS updated
                    """,
                    rows=[{"element_id": row["element_id"], "properties": row["properties"]} for row in segment_rows],
                ).single()
                segment_updates = int(record["updated"] if record else 0)
            cleared_nodes = 0
            if clear_rows:
                record = transaction.run(
                    """
                    UNWIND $rows AS row
                    MATCH (node) WHERE elementId(node)=row.element_id AND NOT node:RouteSegment
                    REMOVE node.riskScore,node.base_risk_score,node.costRiskScore,node.supplier_risk,
                           node.total_risk_score,node.dynamic_risk_score,node.risk_score,
                           node.comprehensive_risk_score,node.risk_level,node.riskLevel,
                           node.risk_breakdown,node.risk_explanation,node.risk_factors,
                           node.riskFactors,node.risk_factors_json
                    SET node += row.properties
                    SET node.risk_recalculated_at=datetime(row.properties.risk_recalculated_at)
                    RETURN count(node) AS updated
                    """,
                    rows=[{"element_id": row["element_id"], "properties": row["properties"]} for row in clear_rows],
                ).single()
                cleared_nodes = int(record["updated"] if record else 0)
            protected_rows = list(
                transaction.run(
                    """
                    MATCH (node)
                    UNWIND [label IN labels(node) WHERE label IN $labels] AS label
                    RETURN label,count(*) AS count
                    """,
                    labels=sorted(PROTECTED_REALTIME_LABELS),
                )
            )
            protected_after = {label: 0 for label in sorted(PROTECTED_REALTIME_LABELS)}
            protected_after.update({row["label"]: int(row["count"]) for row in protected_rows})
            decreased = {
                label: {"before": count, "after": protected_after.get(label, 0)}
                for label, count in protected_before.items()
                if protected_after.get(label, 0) < count
            }
            if decreased:
                raise RuntimeError(f"实时 API 数据数量下降，事务已回滚: {decreased}")
            return {
                "deleted_nodes": deleted_nodes,
                "deleted_relationships": deleted_relationships,
                "segment_updates": segment_updates,
                "cleared_nodes": cleared_nodes,
            }

        with get_driver().session(**self._session_options(WRITE_ACCESS)) as session:
            return session.execute_write(write)


def relationship_backup(relationship: dict[str, Any]) -> dict[str, Any]:
    return {
        "element_id": relationship["element_id"],
        "type": relationship["type"],
        "properties": redact_sensitive_properties(relationship.get("properties") or {}),
        "start": {
            "element_id": relationship["start_element_id"],
            "labels": relationship["start_labels"],
            "properties": redact_sensitive_properties(relationship.get("start_properties") or {}),
        },
        "end": {
            "element_id": relationship["end_element_id"],
            "labels": relationship["end_labels"],
            "properties": redact_sensitive_properties(relationship.get("end_properties") or {}),
        },
    }


def plan_fingerprint(deletion_ids: list[str], segment_rows: list[dict[str, Any]], clear_rows: list[dict[str, Any]]) -> str:
    payload = {
        "deletion_ids": deletion_ids,
        "segments": [
            {"element_id": row["element_id"], "risk_input_hash": row["properties"]["risk_input_hash"]}
            for row in segment_rows
        ],
        "clear_ids": [row["element_id"] for row in clear_rows],
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


def render_report(artifact: dict[str, Any], backup_path: Path, report_path: Path) -> str:
    before = artifact["before"]
    after = artifact.get("after") or {}
    factor_counts = artifact["plan"]["risk_factor_counts"]
    mode_counts: Counter[tuple[str, str]] = Counter(
        (row["mode"], row["result"]["status"]) for row in artifact["plan"]["segment_recalculations"]
    )
    lines = [
        "# 阶段 4：真实 Provider 风险清理与重算报告",
        "",
        f"> 生成时间（UTC）：`{artifact['metadata']['generated_at_utc']}`  ",
        f"> 模式：`{artifact['mode']}`  ",
        f"> 评分版本：`{PROVIDER_RISK_VERSION}`  ",
        f"> 数据库：`{artifact['metadata']['database']}`",
        "",
        "## 1. 结论",
        "",
        f"- 执行状态：`{artifact['execution']['status']}`。",
        f"- 无 Provider 的合成 `RiskFactor` 删除候选：**{len(artifact['plan']['risk_factor_deletion_ids'])}**。",
        f"- 需按运输方式重算的 `RouteSegment`：**{len(artifact['plan']['segment_recalculations'])}**。",
        f"- 需清空无来源通用风险字段的其他节点：**{len(artifact['plan']['clear_rows'])}**。",
        f"- 计划指纹：`{artifact['metadata']['plan_sha256']}`。",
        "",
        "## 2. 安全边界",
        "",
        "- 只允许删除经过来源标记、Provider、Evidence、审核状态和邻接实时数据五项检查的 `RiskFactor`。",
        "- 不删除路线、路段、供应商、地点、GDELT、Open-Meteo、AIS、Shipment 或任何实时观测节点。",
        "- 所有被删节点、关系、被清空字段和重算前属性都先写入 JSON 备份。",
        "- 写事务结束前会复核实时标签数量；任一数量下降则整个事务回滚。",
        "- 风险缺失写为 `null/unavailable`，不再写入 `0.5` 或 `50` 作为中性默认值。",
        "",
        "## 3. 清理统计",
        "",
        markdown_table(
            ["项目", "迁移前", "迁移后"],
            [
                ["节点总数", before["totals"]["nodes"], (after.get("totals") or {}).get("nodes", "未执行")],
                ["关系总数", before["totals"]["relationships"], (after.get("totals") or {}).get("relationships", "未执行")],
                ["RiskFactor", before["statistics"]["summary"].get("risk_factor_nodes"), (after.get("statistics") or {}).get("summary", {}).get("risk_factor_nodes", "未执行")],
                ["无 Provider RiskFactor", before["statistics"]["summary"].get("providerless_risk_factors"), (after.get("statistics") or {}).get("summary", {}).get("providerless_risk_factors", "未执行")],
                ["仍含旧风险字段节点", before["statistics"]["summary"].get("legacy_risk_nodes"), (after.get("statistics") or {}).get("summary", {}).get("legacy_risk_nodes", "未执行")],
            ],
        ),
        "## 4. RiskFactor disposition",
        "",
        markdown_table(["状态", "数量"], [[status, count] for status, count in sorted(factor_counts.items())]),
        "## 5. 分运输方式重算",
        "",
        markdown_table(
            ["运输方式", "结果状态", "数量"],
            [[mode, status, count] for (mode, status), count in sorted(mode_counts.items())],
        ),
        "- 海运：可使用 Open-Meteo 天气和 GDELT 地缘政治；没有 Provider 的海盗、拥堵、制裁、班期不参与。",
        "- 铁路：可使用 Open-Meteo 天气和 GDELT 地缘政治；没有 Provider 的边境、基础设施、班期、制裁不参与。",
        "- 公路：当前只接受 Open-Meteo 天气；GDELT 新闻不会被错误映射成道路交通或边境风险。",
        "- 空运：可使用 Open-Meteo 天气和 GDELT 空域冲突；没有 Provider 的容量、班期、制裁、装卸不参与。",
        "- `multimodal`、`delivery` 等尚未配置可信维度时返回不可用，不伪造风险。",
        "",
        "## 6. 数据完整度",
        "",
        "- `provider_risk_data_completeness` = 已有真实 Provider 的适用权重 / 当前运输方式全部权重。",
        "- 只对可用维度重新归一化计算风险分；完整度单独返回，避免把缺失误当成低风险。",
        "- `provider_risk_missing_factors` 会列出仍缺 Provider 的维度。",
        "",
        "## 7. 实时数据保护基线",
        "",
        markdown_table(
            ["标签", "迁移前", "迁移后"],
            [[label, count, (after.get("protected_baseline") or {}).get(label, "未执行")] for label, count in before["protected_baseline"].items()],
        ),
        "## 8. Provider 风险观测",
        "",
        markdown_table(
            ["Provider", "迁移前", "迁移后"],
            [
                [
                    row["provider"],
                    row["count"],
                    next((item["count"] for item in (after.get("statistics") or {}).get("risk_observations_by_provider", []) if item["provider"] == row["provider"]), "未执行"),
                ]
                for row in before["statistics"]["risk_observations_by_provider"]
            ],
        ),
        "## 9. 产物与命令",
        "",
        f"- 完整 JSON 备份：`{backup_path}`。",
        f"- 中文统计报告：`{report_path}`。",
        "",
        "```bash",
        "python scripts/recalculate_provider_risk.py --dry-run",
        f"python scripts/recalculate_provider_risk.py --execute --confirm {EXECUTE_CONFIRMATION}",
        "```",
        "",
    ]
    return "\n".join(lines)


def timestamped_paths(output_dir: Path, generated_at: datetime) -> tuple[Path, Path]:
    timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    return (
        output_dir / f"risk_cleanup_backup_{timestamp}.json",
        output_dir / f"risk_recalculation_report_{timestamp}.md",
    )


def write_artifacts(artifact: dict[str, Any], backup_path: Path, report_path: Path) -> None:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_report(artifact, backup_path, report_path), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="删除无 Provider 默认风险，并按运输方式使用真实 Provider 重算")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只生成备份与迁移前后计划（默认）")
    mode.add_argument("--execute", action="store_true", help="执行限定清理与幂等重算")
    parser.add_argument("--confirm", default="", help=f"执行时必须填写 {EXECUTE_CONFIRMATION}")
    parser.add_argument("--weather-max-age-hours", type=float, default=6.0, help="Open-Meteo 路线天气有效小时数")
    parser.add_argument("--max-delete", type=int, default=1000, help="最多删除多少个已确认无来源 RiskFactor")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"), help="备份与报告目录")
    args = parser.parse_args()
    if args.max_delete < 0:
        parser.error("--max-delete 不能小于 0")
    if args.weather_max_age_hours <= 0:
        parser.error("--weather-max-age-hours 必须大于 0")
    if args.execute and args.confirm != EXECUTE_CONFIRMATION:
        parser.error(f"--execute 必须同时提供 --confirm {EXECUTE_CONFIRMATION}")
    return args


def main() -> int:
    args = parse_args()
    mode = "execute" if args.execute else "dry-run"
    generated_at = utc_now()
    backup_path, report_path = timestamped_paths(args.output_dir, generated_at)
    repository = RiskRecalculationRepository()
    artifact: dict[str, Any] = {}
    try:
        get_driver().verify_connectivity()
        factors = repository.risk_factors()
        factor_ids = [str(node["element_id"]) for node in factors]
        factor_relationships = repository.attached_relationships(factor_ids)
        factor_plan = build_risk_factor_plan(factors, factor_relationships)
        deletion_ids = factor_plan["deletion_ids"]
        full_relationships = repository.attached_relationships(deletion_ids, full_properties=True)
        strategy = load_strategy()
        segment_rows = [
            row
            for segment in repository.route_segments()
            if (
                row := build_segment_recalculation(
                    segment,
                    now=generated_at,
                    max_weather_age_hours=args.weather_max_age_hours,
                    strategy=strategy,
                )
            )
            is not None
        ]
        clear_rows = build_clear_rows(repository.clearable_nodes(), generated_at)
        before = {
            "totals": repository.totals(),
            "statistics": repository.statistics(),
            "protected_baseline": repository.protected_baseline(),
        }
        plan_sha256 = plan_fingerprint(deletion_ids, segment_rows, clear_rows)
        artifact = {
            "metadata": {
                "generated_at_utc": generated_at.isoformat(),
                "database": repository.settings.database or "default",
                "scoring_version": PROVIDER_RISK_VERSION,
                "plan_sha256": plan_sha256,
                "credentials_redacted": True,
                "sensitive_properties_redacted": True,
            },
            "mode": mode,
            "safety": {
                "deletion_scope": "RiskFactor only",
                "max_delete": args.max_delete,
                "protected_realtime_labels": sorted(PROTECTED_REALTIME_LABELS),
                "protected_counts_must_not_decrease": True,
                "backup_written_before_execute": True,
            },
            "before": before,
            "plan": {
                "risk_factor_counts": factor_plan["counts"],
                "risk_factor_deletion_ids": deletion_ids,
                "risk_factors": [
                    {
                        **node,
                        "properties": redact_sensitive_properties(node.get("properties") or {}),
                        "disposition": factor_plan["dispositions"][str(node["element_id"])],
                    }
                    for node in factors
                ],
                "relationships_attached_to_deletion_candidates": [
                    relationship_backup(relationship) for relationship in full_relationships
                ],
                "segment_recalculations": segment_rows,
                "clear_rows": clear_rows,
            },
            "execution": {
                "status": "dry_run" if not args.execute else "planned",
                "deleted_nodes": 0,
                "deleted_relationships": 0,
                "segment_updates": 0,
                "cleared_nodes": 0,
            },
            "after": {},
        }
        write_artifacts(artifact, backup_path, report_path)
        if args.execute:
            if len(deletion_ids) > args.max_delete:
                artifact["execution"].update(
                    {
                        "status": "aborted_delete_limit",
                        "error": f"删除候选 {len(deletion_ids)} 超过 --max-delete {args.max_delete}",
                    }
                )
                write_artifacts(artifact, backup_path, report_path)
                return 2
            changes = repository.execute(
                deletion_ids,
                segment_rows,
                clear_rows,
                before["protected_baseline"],
            )
            artifact["execution"].update({"status": "completed", **changes})
            artifact["after"] = {
                "totals": repository.totals(),
                "statistics": repository.statistics(),
                "protected_baseline": repository.protected_baseline(),
            }
            write_artifacts(artifact, backup_path, report_path)
    except Exception as exc:
        if artifact:
            artifact["execution"].update({"status": "failed", "error": str(exc)})
            write_artifacts(artifact, backup_path, report_path)
        raise
    finally:
        close_driver()
    print(
        json.dumps(
            {
                "mode": mode,
                "status": artifact["execution"]["status"],
                "risk_factors_to_delete": len(artifact["plan"]["risk_factor_deletion_ids"]),
                "segments_to_recalculate": len(artifact["plan"]["segment_recalculations"]),
                "nodes_to_clear": len(artifact["plan"]["clear_rows"]),
                **{key: artifact["execution"].get(key, 0) for key in ("deleted_nodes", "deleted_relationships", "segment_updates", "cleared_nodes")},
                "backup": str(backup_path),
                "report": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
