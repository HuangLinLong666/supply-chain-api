from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neo4j import READ_ACCESS, WRITE_ACCESS

from database.neo4j_client import close_driver, get_driver, get_settings, to_jsonable


CLEANUP_VERSION = "synthetic-cleanup-v1"
EXECUTE_CONFIRMATION = "DELETE_SYNTHETIC_ONLY"
DEFAULT_SOURCE_MARKERS = (
    "fabricated_for_testing",
    "synthetic",
    "sample",
    "mock",
    "standard_skeleton_reference",
    "external_repos_reference_fields",
    "tesla sec route skeleton",
)
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
INTEGRATION_FIELDS = (
    "integration_id",
    "integrationId",
    "planningIntegrationId",
    "vehicle_network_integration_id",
)
PROTECTED_LABELS = {
    "NewsRiskEvent",
    "NewsRiskZone",
    "WeatherRiskSnapshot",
    "Vessel",
    "VesselObservation",
    "PortTrafficSnapshot",
    "Shipment",
}
PROTECTED_SOURCE_MARKERS = (
    "gdelt",
    "open-meteo",
    "open meteo",
    "aisstream",
    "official_registry",
    "official_schedule",
    "paid_api",
    "open_api",
    "ais_observed",
    "flight_observed",
)
PROTECTED_REVIEW_STATUSES = {"approved", "verified", "reviewed"}
IDENTITY_FIELDS = (
    "id",
    "location_id",
    "unlocode",
    "iata",
    "icao",
    "supplier_id",
    "route_id",
    "routeId",
    "segment_id",
    "segmentId",
    "article_id",
    "zone_id",
    "snapshot_id",
    "observation_id",
    "mmsi",
    "name",
)
REDACTED_VALUE = "[REDACTED]"
SENSITIVE_EXACT_KEYS = {
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "connection_uri",
    "credential",
    "credentials",
    "database_uri",
    "neo4j_uri",
    "password",
    "passwd",
    "private_key",
    "secret",
    "token",
    "uri",
}
SENSITIVE_KEY_TOKENS = {
    "credential",
    "credentials",
    "password",
    "passwd",
    "secret",
    "token",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_filters(values: Iterable[str] | None) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        for item in value.split(","):
            item = item.strip().casefold()
            if item and item not in normalized:
                normalized.append(item)
    return normalized


def is_sensitive_property_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
    compact = normalized.replace("_", "")
    if normalized in SENSITIVE_EXACT_KEYS:
        return True
    if any(
        compact.endswith(suffix)
        for suffix in ("apikey", "accesskey", "privatekey", "clientsecret")
    ):
        return True
    tokens = set(normalized.split("_"))
    return bool(tokens & SENSITIVE_KEY_TOKENS) or normalized.endswith("_auth")


def redact_sensitive_properties(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED_VALUE if is_sensitive_property_key(key) else redact_sensitive_properties(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_properties(item) for item in value]
    if isinstance(value, tuple):
        return [redact_sensitive_properties(item) for item in value]
    return value


def node_source_values(node: dict[str, Any]) -> list[str]:
    properties = node.get("properties", {})
    return [str(properties.get(field) or "") for field in SOURCE_FIELDS]


def protection_reasons(node: dict[str, Any]) -> list[str]:
    labels = set(node.get("labels", []))
    properties = node.get("properties", {})
    reasons: list[str] = []
    protected_labels = sorted(labels & PROTECTED_LABELS)
    if protected_labels:
        reasons.append("protected_label:" + ",".join(protected_labels))
    source_text = " ".join(node_source_values(node)).casefold()
    matched_sources = sorted(marker for marker in PROTECTED_SOURCE_MARKERS if marker in source_text)
    if matched_sources:
        reasons.append("protected_source:" + ",".join(matched_sources))
    review_status = str(properties.get("review_status") or "").casefold()
    if review_status in PROTECTED_REVIEW_STATUSES:
        reasons.append(f"protected_review_status:{review_status}")
    return reasons


def selection_reasons(
    node: dict[str, Any],
    source_filters: list[str],
    label_filters: list[str],
    integration_filters: list[str],
) -> list[str]:
    properties = node.get("properties", {})
    labels = {str(label).casefold() for label in node.get("labels", [])}
    source_values = " ".join(node_source_values(node)).casefold()
    integration_values = " ".join(str(properties.get(field) or "") for field in INTEGRATION_FIELDS).casefold()
    reasons = [f"source:{value}" for value in source_filters if value in source_values]
    reasons.extend(f"label:{value}" for value in label_filters if value in labels)
    reasons.extend(f"integration_id:{value}" for value in integration_filters if value in integration_values)
    return reasons


def endpoint_node(relationship: dict[str, Any], prefix: str) -> dict[str, Any]:
    return {
        "element_id": relationship[f"{prefix}_element_id"],
        "labels": relationship[f"{prefix}_labels"],
        "properties": relationship[f"{prefix}_properties"],
    }


@dataclass
class CleanupPlan:
    selected_nodes: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    dispositions: dict[str, dict[str, Any]]
    deletion_ids: list[str]

    @property
    def counts(self) -> dict[str, int]:
        result = Counter(item["status"] for item in self.dispositions.values())
        result["selected_total"] = len(self.selected_nodes)
        result["deletion_candidates"] = len(self.deletion_ids)
        return dict(sorted(result.items()))


def build_cleanup_plan(
    selected_nodes: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    allow_boundary_links: bool = False,
) -> CleanupPlan:
    selected_by_id = {str(node["element_id"]): node for node in selected_nodes}
    hard_protected: dict[str, list[str]] = {}
    for element_id, node in selected_by_id.items():
        reasons = protection_reasons(node)
        if reasons:
            hard_protected[element_id] = reasons
    candidate_ids = set(selected_by_id) - set(hard_protected)
    adjacency: dict[str, set[str]] = defaultdict(set)
    blocked_reasons: dict[str, set[str]] = defaultdict(set)

    for relationship in relationships:
        start_id = str(relationship["start_element_id"])
        end_id = str(relationship["end_element_id"])
        if start_id in candidate_ids and end_id in candidate_ids:
            adjacency[start_id].add(end_id)
            adjacency[end_id].add(start_id)
            continue
        for candidate_id, neighbor_id, neighbor_prefix in (
            (start_id, end_id, "end"),
            (end_id, start_id, "start"),
        ):
            if candidate_id not in candidate_ids or neighbor_id in candidate_ids:
                continue
            neighbor = selected_by_id.get(neighbor_id) or endpoint_node(relationship, neighbor_prefix)
            neighbor_protection = protection_reasons(neighbor)
            if neighbor_id in hard_protected:
                neighbor_protection.extend(hard_protected[neighbor_id])
            if neighbor_protection:
                blocked_reasons[candidate_id].add(
                    "protected_link:" + relationship["type"] + ":" + ",".join(sorted(set(neighbor_protection)))
                )
            elif not allow_boundary_links:
                blocked_reasons[candidate_id].add(
                    "retained_boundary_link:" + relationship["type"] + ":" + ",".join(neighbor.get("labels", []))
                )

    queue = deque(blocked_reasons)
    while queue:
        blocked_id = queue.popleft()
        for neighbor_id in adjacency.get(blocked_id, set()):
            if neighbor_id in blocked_reasons:
                continue
            blocked_reasons[neighbor_id].add(f"depends_on_blocked_candidate:{blocked_id}")
            queue.append(neighbor_id)

    deletion_ids = sorted(candidate_ids - set(blocked_reasons))
    dispositions: dict[str, dict[str, Any]] = {}
    for element_id in selected_by_id:
        if element_id in hard_protected:
            dispositions[element_id] = {"status": "protected", "reasons": sorted(hard_protected[element_id])}
        elif element_id in blocked_reasons:
            dispositions[element_id] = {"status": "blocked", "reasons": sorted(blocked_reasons[element_id])}
        else:
            dispositions[element_id] = {"status": "candidate", "reasons": []}
    return CleanupPlan(selected_nodes, relationships, dispositions, deletion_ids)


class CleanupRepository:
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

    def total_counts(self) -> dict[str, int]:
        rows = self.read("MATCH (n) WITH count(n) AS nodes MATCH ()-[r]->() RETURN nodes,count(r) AS relationships")
        return {"nodes": int(rows[0]["nodes"]), "relationships": int(rows[0]["relationships"])} if rows else {"nodes": 0, "relationships": 0}

    def protected_baseline(self) -> dict[str, int]:
        rows = self.read(
            """
            MATCH (n)
            UNWIND [label IN labels(n) WHERE label IN $labels] AS label
            RETURN label,count(*) AS count ORDER BY label
            """,
            {"labels": sorted(PROTECTED_LABELS)},
        )
        baseline = {label: 0 for label in sorted(PROTECTED_LABELS)}
        baseline.update({row["label"]: int(row["count"]) for row in rows})
        return baseline

    def select_nodes(
        self,
        source_filters: list[str],
        label_filters: list[str],
        integration_filters: list[str],
    ) -> list[dict[str, Any]]:
        return self.read(
            """
            MATCH (n)
            WITH n,
                 [field IN $source_fields | toLower(toString(coalesce(n[field],'')))] AS source_values,
                 [field IN $integration_fields | toLower(toString(coalesce(n[field],'')))] AS integration_values,
                 [label IN labels(n) | toLower(label)] AS normalized_labels
            WHERE ($use_source_filter=false OR any(filter IN $source_filters WHERE any(value IN source_values WHERE value CONTAINS filter)))
              AND ($use_label_filter=false OR any(filter IN $label_filters WHERE filter IN normalized_labels))
              AND ($use_integration_filter=false OR any(filter IN $integration_filters WHERE any(value IN integration_values WHERE value CONTAINS filter)))
            RETURN elementId(n) AS element_id,labels(n) AS labels,properties(n) AS properties
            ORDER BY element_id
            """,
            {
                "source_fields": list(SOURCE_FIELDS),
                "integration_fields": list(INTEGRATION_FIELDS),
                "source_filters": source_filters,
                "label_filters": label_filters,
                "integration_filters": integration_filters,
                "use_source_filter": bool(source_filters),
                "use_label_filter": bool(label_filters),
                "use_integration_filter": bool(integration_filters),
            },
        )

    def attached_relationships(self, element_ids: list[str]) -> list[dict[str, Any]]:
        if not element_ids:
            return []
        return self.read(
            """
            MATCH (selected)
            WHERE elementId(selected) IN $element_ids
            MATCH (selected)-[relationship]-()
            WITH DISTINCT relationship,startNode(relationship) AS start,endNode(relationship) AS end
            RETURN elementId(relationship) AS element_id,type(relationship) AS type,
                   {} AS properties,
                   elementId(start) AS start_element_id,labels(start) AS start_labels,
                   {
                     provider:start.provider,source:start.source,source_type:start.source_type,
                     data_source:start.data_source,dataSource:start.dataSource,modelSource:start.modelSource,
                     source_repo:start.source_repo,weather_source:start.weather_source,
                     marine_source:start.marine_source,review_status:start.review_status
                   } AS start_properties,
                   elementId(end) AS end_element_id,labels(end) AS end_labels,
                   {
                     provider:end.provider,source:end.source,source_type:end.source_type,
                     data_source:end.data_source,dataSource:end.dataSource,modelSource:end.modelSource,
                     source_repo:end.source_repo,weather_source:end.weather_source,
                     marine_source:end.marine_source,review_status:end.review_status
                   } AS end_properties
            ORDER BY element_id
            """,
            {"element_ids": element_ids},
        )

    def backup_relationships(self, element_ids: list[str]) -> list[dict[str, Any]]:
        if not element_ids:
            return []
        return self.read(
            """
            MATCH (selected)
            WHERE elementId(selected) IN $element_ids
            MATCH (selected)-[relationship]-()
            WITH DISTINCT relationship,startNode(relationship) AS start,endNode(relationship) AS end
            RETURN elementId(relationship) AS element_id,type(relationship) AS type,
                   properties(relationship) AS properties,
                   elementId(start) AS start_element_id,labels(start) AS start_labels,properties(start) AS start_properties,
                   elementId(end) AS end_element_id,labels(end) AS end_labels,properties(end) AS end_properties
            ORDER BY element_id
            """,
            {"element_ids": element_ids},
        )

    def delete_nodes(self, element_ids: list[str], protected_before: dict[str, int]) -> int:
        if not element_ids:
            return 0

        def delete_and_verify(transaction):
            record = transaction.run(
                """
                UNWIND $element_ids AS element_id
                MATCH (node) WHERE elementId(node)=element_id
                DETACH DELETE node
                RETURN count(*) AS deleted
                """,
                element_ids=element_ids,
            ).single()
            protected_rows = list(
                transaction.run(
                    """
                    MATCH (n)
                    UNWIND [label IN labels(n) WHERE label IN $labels] AS label
                    RETURN label,count(*) AS count
                    """,
                    labels=sorted(PROTECTED_LABELS),
                )
            )
            protected_after = {row["label"]: int(row["count"]) for row in protected_rows}
            if protected_after != protected_before:
                raise RuntimeError(
                    f"保护数据数量发生变化，事务已回滚: before={protected_before}, after={protected_after}"
                )
            return int(record["deleted"] if record else 0)

        with get_driver().session(**self._session_options(WRITE_ACCESS)) as session:
            return session.execute_write(delete_and_verify)


def identity_snapshot(properties: dict[str, Any]) -> dict[str, Any]:
    return {field: properties[field] for field in IDENTITY_FIELDS if properties.get(field) is not None}


def distribution(nodes: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    if key == "label":
        for node in nodes:
            counts.update(str(label) for label in node.get("labels", []))
    elif key == "source":
        for node in nodes:
            values = sorted(set(value for value in node_source_values(node) if value))
            counts.update(values or ["unattributed"])
    return [{key: value, "count": count} for value, count in counts.most_common()]


def reason_category(reason: str) -> str:
    if reason.startswith("protected_link:") or reason.startswith("retained_boundary_link:"):
        return ":".join(reason.split(":", 2)[:2])
    if reason.startswith("depends_on_blocked_candidate:"):
        return "depends_on_blocked_candidate"
    return reason


def disposition_reason_distribution(plan: CleanupPlan, status: str) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for disposition in plan.dispositions.values():
        if disposition["status"] == status:
            counts.update(reason_category(reason) for reason in disposition["reasons"])
    return [{"reason": reason, "count": count} for reason, count in counts.most_common()]


def relationship_backup(relationship: dict[str, Any]) -> dict[str, Any]:
    return {
        "element_id": relationship["element_id"],
        "type": relationship["type"],
        "properties": redact_sensitive_properties(relationship["properties"]),
        "start": {
            "element_id": relationship["start_element_id"],
            "labels": relationship["start_labels"],
            "identity": identity_snapshot(relationship["start_properties"]),
        },
        "end": {
            "element_id": relationship["end_element_id"],
            "labels": relationship["end_labels"],
            "identity": identity_snapshot(relationship["end_properties"]),
        },
    }


def build_artifact(
    plan: CleanupPlan,
    backup_relationship_rows: list[dict[str, Any]],
    source_filters: list[str],
    label_filters: list[str],
    integration_filters: list[str],
    default_filters_used: bool,
    allow_boundary_links: bool,
    mode: str,
    database: str,
    totals_before: dict[str, int],
    protected_before: dict[str, int],
    generated_at: datetime,
) -> dict[str, Any]:
    deletion_ids = set(plan.deletion_ids)
    backup_relationships = [
        relationship_backup(relationship)
        for relationship in backup_relationship_rows
        if relationship["start_element_id"] in deletion_ids or relationship["end_element_id"] in deletion_ids
    ]
    nodes: list[dict[str, Any]] = []
    for node in plan.selected_nodes:
        element_id = str(node["element_id"])
        disposition = plan.dispositions[element_id]
        nodes.append(
            {
                "element_id": node["element_id"],
                "labels": node["labels"],
                "properties": redact_sensitive_properties(node["properties"]),
                "selection_reasons": selection_reasons(node, source_filters, label_filters, integration_filters),
                "disposition": disposition,
            }
        )
    candidate_nodes = [node for node in plan.selected_nodes if str(node["element_id"]) in deletion_ids]
    deletion_plan_payload = {
        "node_element_ids": sorted(deletion_ids),
        "relationship_element_ids": sorted(
            str(relationship["element_id"]) for relationship in backup_relationships
        ),
    }
    deletion_plan_sha256 = hashlib.sha256(
        json.dumps(deletion_plan_payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "metadata": {
            "cleanup_version": CLEANUP_VERSION,
            "generated_at_utc": generated_at.isoformat(),
            "database": database,
            "credentials_redacted": True,
            "sensitive_properties_redacted": True,
            "database_write_requested": mode == "execute",
        },
        "mode": mode,
        "filters": {
            "sources": source_filters,
            "labels": label_filters,
            "integration_ids": integration_filters,
            "default_source_markers_used": default_filters_used,
            "match_semantics": "不同过滤类别之间为 AND，同一类别内为 OR，字符串匹配不区分大小写",
        },
        "safety": {
            "protected_labels": sorted(PROTECTED_LABELS),
            "protected_source_markers": list(PROTECTED_SOURCE_MARKERS),
            "protected_review_statuses": sorted(PROTECTED_REVIEW_STATUSES),
            "allow_boundary_links": allow_boundary_links,
            "protected_components_are_never_deleted": True,
            "deletion_plan_sha256": deletion_plan_sha256,
        },
        "database_before": totals_before,
        "protected_baseline_before": protected_before,
        "plan_counts": plan.counts,
        "selected_label_distribution": distribution(plan.selected_nodes, "label"),
        "selected_source_distribution": distribution(plan.selected_nodes, "source"),
        "protected_reason_distribution": disposition_reason_distribution(plan, "protected"),
        "blocked_reason_distribution": disposition_reason_distribution(plan, "blocked"),
        "candidate_label_distribution": distribution(candidate_nodes, "label"),
        "candidate_source_distribution": distribution(candidate_nodes, "source"),
        "selected_nodes": nodes,
        "relationships_attached_to_deletion_candidates": backup_relationships,
        "execution": {
            "status": "dry_run" if mode == "dry-run" else "planned",
            "deleted_nodes": 0,
            "database_after": None,
            "protected_baseline_after": None,
        },
    }


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "_无记录_\n"
    result = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        values = [str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ") for value in row]
        result.append("| " + " | ".join(values) + " |")
    return "\n".join(result) + "\n"


def render_report(artifact: dict[str, Any], backup_path: Path, report_path: Path) -> str:
    counts = artifact["plan_counts"]
    selected = artifact["selected_nodes"]
    protected_samples = [node for node in selected if node["disposition"]["status"] == "protected"][:20]
    blocked_samples = [node for node in selected if node["disposition"]["status"] == "blocked"][:30]
    candidate_samples = [node for node in selected if node["disposition"]["status"] == "candidate"][:30]
    lines = [
        "# Neo4j 合成数据安全清理报告",
        "",
        f"> 生成时间（UTC）：`{artifact['metadata']['generated_at_utc']}`  ",
        f"> 模式：`{artifact['mode']}`  ",
        f"> 数据库：`{artifact['metadata']['database']}`  ",
        f"> 清理版本：`{artifact['metadata']['cleanup_version']}`",
        "",
        "## 1. 执行结论",
        "",
    ]
    if artifact["mode"] == "dry-run":
        lines.append("- 本次仅执行 **dry-run**，数据库新增、修改、删除均为 **0**。")
    else:
        lines.append(f"- 执行状态：`{artifact['execution']['status']}`；实际删除节点：**{artifact['execution']['deleted_nodes']}**。")
    lines.extend(
        [
            f"- 共匹配 **{counts.get('selected_total', 0)}** 个节点。",
            f"- 硬保护节点 **{counts.get('protected', 0)}** 个。",
            f"- 因保护关系、保留边界或依赖传播而阻止 **{counts.get('blocked', 0)}** 个。",
            f"- 最终可删除候选 **{counts.get('deletion_candidates', 0)}** 个。",
            "",
            "## 2. 过滤条件",
            "",
            markdown_table(
                ["类别", "值"],
                [
                    ["source", ", ".join(artifact["filters"]["sources"]) or "未限制"],
                    ["label", ", ".join(artifact["filters"]["labels"]) or "未限制"],
                    ["integration_id", ", ".join(artifact["filters"]["integration_ids"]) or "未限制"],
                    ["是否使用默认来源标记", artifact["filters"]["default_source_markers_used"]],
                    ["匹配语义", artifact["filters"]["match_semantics"]],
                ],
            ),
            "## 3. 安全规则",
            "",
            "- `NewsRiskEvent`、`NewsRiskZone`、`WeatherRiskSnapshot`、`Vessel`、`VesselObservation`、`PortTrafficSnapshot` 和 `Shipment` 永不进入删除集合。",
            "- GDELT、Open-Meteo、AISStream、官方注册表、官方班期和已审核数据受到来源保护。",
            "- 与受保护节点相连的候选节点会被阻止，阻止状态会沿候选子图传播，避免只删除风险/成本子节点而破坏保留路线。",
            "- 默认不允许候选节点删除与保留节点之间的边界关系；只有显式传入 `--allow-boundary-links` 才会放宽普通边界，但实时保护边界永不放宽。",
            "- 执行删除必须显式提供至少一个 `--source`、`--label` 或 `--integration-id`，同时提供 `--execute --confirm DELETE_SYNTHETIC_ONLY`，并且不能超过 `--max-delete` 上限。",
            "",
            "## 4. 匹配范围与阻止原因",
            "",
            "### 4.1 全部匹配节点标签",
            "",
            markdown_table(["标签", "数量"], [[row["label"], row["count"]] for row in artifact["selected_label_distribution"]]),
            "### 4.2 全部匹配节点来源",
            "",
            markdown_table(["来源", "数量"], [[row["source"], row["count"]] for row in artifact["selected_source_distribution"]]),
            "### 4.3 硬保护原因",
            "",
            markdown_table(["原因", "数量"], [[row["reason"], row["count"]] for row in artifact["protected_reason_distribution"]]),
            "### 4.4 阻止原因",
            "",
            markdown_table(["原因", "数量"], [[row["reason"], row["count"]] for row in artifact["blocked_reason_distribution"]]),
            "## 5. 最终删除候选分布",
            "",
            "### 5.1 标签",
            "",
            markdown_table(["标签", "数量"], [[row["label"], row["count"]] for row in artifact["candidate_label_distribution"]]),
            "### 5.2 来源",
            "",
            markdown_table(["来源", "数量"], [[row["source"], row["count"]] for row in artifact["candidate_source_distribution"]]),
            "## 6. 实时数据保护基线",
            "",
            markdown_table(["标签", "清理前", "清理后"], [[label, count, (artifact["execution"].get("protected_baseline_after") or {}).get(label, "未执行")] for label, count in artifact["protected_baseline_before"].items()]),
            "## 7. 样例",
            "",
            "### 7.1 硬保护节点",
            "",
            markdown_table(
                ["elementId", "标签", "业务标识", "原因"],
                [[node["element_id"], ", ".join(node["labels"]), json.dumps(identity_snapshot(node["properties"]), ensure_ascii=False), "; ".join(node["disposition"]["reasons"])] for node in protected_samples],
            ),
            "### 7.2 被阻止候选",
            "",
            markdown_table(
                ["elementId", "标签", "业务标识", "原因"],
                [[node["element_id"], ", ".join(node["labels"]), json.dumps(identity_snapshot(node["properties"]), ensure_ascii=False), "; ".join(node["disposition"]["reasons"][:3])] for node in blocked_samples],
            ),
            "### 7.3 可删除候选",
            "",
            markdown_table(
                ["elementId", "标签", "业务标识", "匹配原因"],
                [[node["element_id"], ", ".join(node["labels"]), json.dumps(identity_snapshot(node["properties"]), ensure_ascii=False), "; ".join(node["selection_reasons"])] for node in candidate_samples],
            ),
            "## 8. 备份与恢复信息",
            "",
            f"- 机器可读备份：`{backup_path}`。",
            f"- 本报告：`{report_path}`。",
            f"- 备份包含所有匹配节点及其 disposition，以及可删除候选附着的 **{len(artifact['relationships_attached_to_deletion_candidates'])}** 条关系。",
            f"- 删除计划指纹（SHA-256）：`{artifact['safety']['deletion_plan_sha256']}`。",
            "- 节点和关系属性中的密码、令牌、API Key、认证信息与数据库连接 URI 会在备份中替换为 `[REDACTED]`。",
            "- 本阶段只设计和预览清理；恢复脚本将在确认实际清理方案后再实现，避免对错误候选产生二次写入。",
            "",
            "## 9. 命令",
            "",
            "默认安全预览：",
            "",
            "```bash",
            "python scripts/cleanup_synthetic_data.py --dry-run",
            "```",
            "",
            "按来源、标签和 integration_id 缩小范围：",
            "",
            "```bash",
            "python scripts/cleanup_synthetic_data.py --dry-run \\",
            "  --source standard_skeleton_reference \\",
            "  --label RouteSegment \\",
            "  --integration-id your-integration-id",
            "```",
            "",
            "实际执行示例（本报告未执行）：",
            "",
            "```bash",
            "python scripts/cleanup_synthetic_data.py --execute \\",
            "  --source standard_skeleton_reference \\",
            "  --confirm DELETE_SYNTHETIC_ONLY \\",
            "  --max-delete 100",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def timestamped_paths(output_dir: Path, generated_at: datetime) -> tuple[Path, Path]:
    timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    return (
        output_dir / f"database_cleanup_backup_{timestamp}.json",
        output_dir / f"database_cleanup_report_{timestamp}.md",
    )


def write_artifacts(artifact: dict[str, Any], backup_path: Path, report_path: Path) -> None:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_report(artifact, backup_path, report_path), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="安全预览或删除 Neo4j 中明确标记的合成/测试数据")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只生成备份和报告，不修改数据库（默认）")
    mode.add_argument("--execute", action="store_true", help="按计划执行限定删除")
    parser.add_argument("--source", action="append", help="按 source/provider/source_type 等字段过滤，可重复或逗号分隔")
    parser.add_argument("--label", action="append", help="按节点标签过滤，可重复或逗号分隔")
    parser.add_argument("--integration-id", action="append", help="按 integration_id 兼容字段过滤，可重复或逗号分隔")
    parser.add_argument("--allow-boundary-links", action="store_true", help="允许删除候选与普通保留节点之间的关系；不会放宽实时数据保护")
    parser.add_argument("--confirm", default="", help=f"执行删除时必须填写 {EXECUTE_CONFIRMATION}")
    parser.add_argument("--max-delete", type=int, default=1000, help="单次允许删除的最大节点数，默认 1000")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"), help="备份和报告输出目录")
    args = parser.parse_args()
    if args.max_delete < 0:
        parser.error("--max-delete 不能小于 0")
    if args.execute and args.confirm != EXECUTE_CONFIRMATION:
        parser.error(f"--execute 必须同时提供 --confirm {EXECUTE_CONFIRMATION}")
    if args.execute and not any(
        (
            normalize_filters(args.source),
            normalize_filters(args.label),
            normalize_filters(args.integration_id),
        )
    ):
        parser.error("--execute 必须显式提供至少一个 --source、--label 或 --integration-id")
    return args


def main() -> int:
    args = parse_args()
    source_filters = normalize_filters(args.source)
    label_filters = normalize_filters(args.label)
    integration_filters = normalize_filters(args.integration_id)
    default_filters_used = not source_filters and not label_filters and not integration_filters
    if default_filters_used:
        source_filters = list(DEFAULT_SOURCE_MARKERS)
    mode = "execute" if args.execute else "dry-run"
    generated_at = utc_now()
    backup_path, report_path = timestamped_paths(args.output_dir, generated_at)
    repository = CleanupRepository()
    try:
        get_driver().verify_connectivity()
        totals_before = repository.total_counts()
        protected_before = repository.protected_baseline()
        selected_nodes = repository.select_nodes(source_filters, label_filters, integration_filters)
        relationship_seed_ids = [
            str(node["element_id"])
            for node in selected_nodes
            if not protection_reasons(node)
        ]
        relationships = repository.attached_relationships(relationship_seed_ids)
        plan = build_cleanup_plan(selected_nodes, relationships, args.allow_boundary_links)
        backup_relationship_rows = repository.backup_relationships(plan.deletion_ids)
        artifact = build_artifact(
            plan=plan,
            backup_relationship_rows=backup_relationship_rows,
            source_filters=source_filters,
            label_filters=label_filters,
            integration_filters=integration_filters,
            default_filters_used=default_filters_used,
            allow_boundary_links=args.allow_boundary_links,
            mode=mode,
            database=repository.settings.database or "default",
            totals_before=totals_before,
            protected_before=protected_before,
            generated_at=generated_at,
        )
        write_artifacts(artifact, backup_path, report_path)
        if args.execute:
            if len(plan.deletion_ids) > args.max_delete:
                artifact["execution"]["status"] = "aborted_max_delete_exceeded"
                artifact["execution"]["error"] = (
                    f"候选 {len(plan.deletion_ids)} 超过 --max-delete {args.max_delete}，未修改数据库"
                )
                write_artifacts(artifact, backup_path, report_path)
                raise SystemExit(2)
            deleted = repository.delete_nodes(plan.deletion_ids, protected_before)
            artifact["execution"].update(
                {
                    "status": "completed",
                    "deleted_nodes": deleted,
                    "database_after": repository.total_counts(),
                    "protected_baseline_after": repository.protected_baseline(),
                }
            )
            write_artifacts(artifact, backup_path, report_path)
    finally:
        close_driver()
    print(
        json.dumps(
            {
                "mode": mode,
                "database_writes": artifact["execution"]["deleted_nodes"],
                "selected": artifact["plan_counts"].get("selected_total", 0),
                "protected": artifact["plan_counts"].get("protected", 0),
                "blocked": artifact["plan_counts"].get("blocked", 0),
                "deletion_candidates": artifact["plan_counts"].get("deletion_candidates", 0),
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
