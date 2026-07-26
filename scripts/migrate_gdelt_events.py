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

from database.neo4j_client import close_driver, run_query
from database.unified_schema import SCHEMA_VERSION
from gdelt.repository import ensure_schema
from gdelt.risk import (
    GDELT_SCORING_VERSION,
    canonicalize_url,
    classify_article,
    clean_text,
    cluster_articles,
    freshness_weight,
    normalize_title,
    prepare_article,
)


EXECUTE_CONFIRMATION = "MIGRATE_GDELT_EVENTS_V3"
EVENT_FIELDS = (
    "canonical_url",
    "canonical_url_hash",
    "normalized_title",
    "content_hash",
    "event_category",
    "matched_categories",
    "matched_terms",
    "severity",
    "classification_status",
    "time_status",
    "source_credibility_status",
    "scoring_version",
)


def read_events() -> list[dict[str, Any]]:
    return run_query(
        """
        MATCH (event:NewsRiskEvent)
        OPTIONAL MATCH (event)-[:AFFECTS_ZONE]->(zone:NewsRiskZone)
        RETURN elementId(event) AS element_id,event.article_id AS article_id,
               event.title AS title,event.url AS url,event.domain AS domain,
               event.language AS language,event.source_country AS source_country,
               event.seen_at AS seen_at,collect(DISTINCT zone.zone_id) AS zone_ids,
               event.canonical_url AS canonical_url,
               event.canonical_url_hash AS canonical_url_hash,
               event.normalized_title AS normalized_title,event.content_hash AS content_hash,
               event.event_category AS event_category,
               event.matched_categories AS matched_categories,event.matched_terms AS matched_terms,
               event.severity AS severity,event.classification_status AS classification_status,
               event.time_status AS time_status,
               event.source_credibility_status AS source_credibility_status,
               event.scoring_version AS scoring_version
        ORDER BY event.article_id
        """
    )


def read_existing_cluster_ids() -> set[str]:
    return {
        str(row["cluster_id"])
        for row in run_query("MATCH (cluster:NewsRiskCluster) RETURN cluster.cluster_id AS cluster_id")
        if row.get("cluster_id")
    }


def read_existing_memberships() -> set[tuple[str, str]]:
    return {
        (str(row["article_id"]), str(row["cluster_id"]))
        for row in run_query(
            """
            MATCH (event:NewsRiskEvent)-[:MEMBER_OF_EVENT_CLUSTER]->(cluster:NewsRiskCluster)
            RETURN event.article_id AS article_id,cluster.cluster_id AS cluster_id
            """
        )
        if row.get("article_id") and row.get("cluster_id")
    }


def comparable(value: Any) -> Any:
    if isinstance(value, list):
        return sorted(comparable(item) for item in value)
    if isinstance(value, dict):
        return {key: comparable(item) for key, item in sorted(value.items())}
    return value


def build_migration_plan(
    events: list[dict[str, Any]],
    *,
    now: datetime,
    existing_cluster_ids: set[str] | None = None,
    existing_memberships: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    existing_cluster_ids = existing_cluster_ids or set()
    existing_memberships = existing_memberships or set()
    event_updates: list[dict[str, Any]] = []
    valid_by_zone: dict[str, list[dict[str, Any]]] = {}
    rejected = Counter()
    categories = Counter()
    zone_raw_counts = Counter()
    zone_valid_counts = Counter()
    zone_category_counts: dict[str, Counter[str]] = {}
    zone_rejected_counts: dict[str, Counter[str]] = {}

    for event in events:
        event_zone_ids = [str(zone_id) for zone_id in event.get("zone_ids") or ["unassigned"]]
        for zone_id in event_zone_ids:
            zone_raw_counts[zone_id] += 1
        prepared, reason = prepare_article(
            {
                "article_id": event.get("article_id"),
                "title": event.get("title"),
                "url": event.get("url"),
                "domain": event.get("domain"),
                "language": event.get("language"),
                "source_country": event.get("source_country"),
                "seen_at": event.get("seen_at"),
            },
            now=now,
        )
        if prepared is None:
            rejected[reason or "invalid"] += 1
            for zone_id in event_zone_ids:
                zone_rejected_counts.setdefault(zone_id, Counter())[reason or "invalid"] += 1
            classification = classify_article(event)
            canonical_url = canonicalize_url(event.get("url"))
            after = {
                "canonical_url": canonical_url,
                "canonical_url_hash": hashlib.sha256(
                    str(canonical_url or event.get("article_id") or "").encode("utf-8")
                ).hexdigest()[:24],
                "normalized_title": normalize_title(event.get("title")),
                "content_hash": None,
                **classification,
                "time_status": reason or "invalid",
                "source_credibility_status": "unavailable",
                "scoring_version": GDELT_SCORING_VERSION,
            }
        else:
            categories[prepared["event_category"]] += 1
            after = {field: prepared.get(field) for field in EVENT_FIELDS}
            for zone_id in event_zone_ids:
                valid_by_zone.setdefault(zone_id, []).append(dict(prepared))
                zone_valid_counts[zone_id] += 1
                zone_category_counts.setdefault(zone_id, Counter())[prepared["event_category"]] += 1
        before = {field: event.get(field) for field in EVENT_FIELDS}
        if comparable(before) != comparable(after):
            event_updates.append({"element_id": event["element_id"], "properties": after})

    clusters: list[dict[str, Any]] = []
    memberships: list[dict[str, str]] = []
    zone_cluster_counts = Counter()
    for zone_id, prepared_events in sorted(valid_by_zone.items()):
        zone_clusters = cluster_articles(prepared_events, namespace=zone_id)
        zone_cluster_counts[zone_id] = len(zone_clusters)
        for cluster in zone_clusters:
            cluster["freshness_weight"] = round(
                freshness_weight(cluster["last_seen"], now=now, half_life_hours=24), 4
            )
            cluster["effective_severity"] = round(
                float(cluster["severity"]) * cluster["freshness_weight"], 4
            )
            cluster["zone_id"] = zone_id
            if cluster["cluster_id"] not in existing_cluster_ids:
                clusters.append(cluster)
            for article_id in cluster["article_ids"]:
                key = (str(article_id), str(cluster["cluster_id"]))
                if key not in existing_memberships:
                    memberships.append(
                        {
                            "article_id": key[0],
                            "cluster_id": key[1],
                            "zone_id": zone_id,
                        }
                    )

    zone_metadata = [
        {
            "zone_id": zone_id,
            "raw_article_count": zone_raw_counts[zone_id],
            "valid_article_count": zone_valid_counts[zone_id],
            "cluster_count": zone_cluster_counts[zone_id],
            "category_counts_json": json.dumps(
                dict(zone_category_counts.get(zone_id, {})), ensure_ascii=False, sort_keys=True
            ),
            "rejected_counts_json": json.dumps(
                dict(zone_rejected_counts.get(zone_id, {})), ensure_ascii=False, sort_keys=True
            ),
        }
        for zone_id in sorted(zone_raw_counts)
        if zone_id != "unassigned"
    ]
    fingerprint = {
        "event_updates": [row["element_id"] for row in event_updates],
        "clusters": [row["cluster_id"] for row in clusters],
        "memberships": [f"{row['article_id']}|{row['cluster_id']}" for row in memberships],
        "zone_metadata": zone_metadata,
    }
    plan_hash = hashlib.sha256(
        json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "scoring_version": GDELT_SCORING_VERSION,
        "generated_at": now.isoformat(),
        "plan_hash": plan_hash,
        "events_scanned": len(events),
        "event_updates": event_updates,
        "clusters": clusters,
        "memberships": memberships,
        "zone_metadata": zone_metadata,
        "category_counts": dict(categories),
        "rejected_counts": dict(rejected),
        "planned_event_updates": len(event_updates),
        "planned_cluster_creates": len(clusters),
        "planned_membership_creates": len(memberships),
    }


def chunks(rows: list[dict[str, Any]], size: int = 500) -> list[list[dict[str, Any]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def execute_plan(plan: dict[str, Any]) -> dict[str, int]:
    ensure_schema()
    event_updates = 0
    cluster_creates = 0
    membership_creates = 0
    zone_metadata_updates = 0
    for batch in chunks(plan["event_updates"]):
        rows = run_query(
            """
            UNWIND $rows AS row
            MATCH (event:NewsRiskEvent) WHERE elementId(event)=row.element_id
            SET event += row.properties,event.schema_version=$schema_version
            RETURN count(event) AS updated
            """,
            {"rows": batch, "schema_version": SCHEMA_VERSION},
        )
        event_updates += int(rows[0]["updated"] if rows else 0)
    for batch in chunks(plan["clusters"]):
        rows = run_query(
            """
            UNWIND $rows AS row
            MERGE (cluster:NewsRiskCluster {cluster_id:row.cluster_id})
            SET cluster:RiskObservation,cluster.event_category=row.event_category,
                cluster.representative_title=row.representative_title,
                cluster.representative_article_id=row.representative_article_id,
                cluster.first_seen=datetime(row.first_seen),cluster.last_seen=datetime(row.last_seen),
                cluster.observed_at=datetime(row.last_seen),cluster.collected_at=datetime($collected_at),
                cluster.severity=row.severity,cluster.freshness_weight=row.freshness_weight,
                cluster.effective_severity=row.effective_severity,
                cluster.article_count=row.article_count,
                cluster.distinct_domain_count=row.distinct_domain_count,cluster.domains=row.domains,
                cluster.provider='GDELT',cluster.source='GDELT DOC 2.0 API event cluster',
                cluster.source_type='open_api',cluster.data_status='estimated',cluster.is_inferred=true,
                cluster.calculation_status='derived_from_observed_events',
                cluster.status='available',cluster.source_credibility_status='unavailable',
                cluster.confidence_basis='source_diversity_not_domain_credibility',
                cluster.observation_id=row.cluster_id,cluster.observation_type='news_event_cluster',
                cluster.scoring_version=$version,cluster.schema_version=$schema_version
            WITH row,cluster
            OPTIONAL MATCH (zone:NewsRiskZone {zone_id:row.zone_id})
            FOREACH (_ IN CASE WHEN zone IS NULL THEN [] ELSE [1] END |
              MERGE (cluster)-[:AFFECTS_ZONE]->(zone)
            )
            RETURN count(cluster) AS updated
            """,
            {
                "rows": batch,
                "collected_at": plan["generated_at"],
                "version": GDELT_SCORING_VERSION,
                "schema_version": SCHEMA_VERSION,
            },
        )
        cluster_creates += int(rows[0]["updated"] if rows else 0)
    for batch in chunks(plan["memberships"]):
        rows = run_query(
            """
            UNWIND $rows AS row
            MATCH (event:NewsRiskEvent {article_id:row.article_id})
            MATCH (cluster:NewsRiskCluster {cluster_id:row.cluster_id})
            MERGE (event)-[membership:MEMBER_OF_EVENT_CLUSTER]->(cluster)
            SET membership.zone_id=row.zone_id,membership.scoring_version=$version
            RETURN count(membership) AS updated
            """,
            {"rows": batch, "version": GDELT_SCORING_VERSION},
        )
        membership_creates += int(rows[0]["updated"] if rows else 0)
    if plan["zone_metadata"]:
        rows = run_query(
            """
            UNWIND $rows AS row
            MATCH (zone:NewsRiskZone {zone_id:row.zone_id})
            SET zone.raw_article_count=row.raw_article_count,
                zone.valid_article_count=row.valid_article_count,
                zone.event_cluster_count=row.cluster_count,
                zone.category_counts_json=row.category_counts_json,
                zone.rejected_counts_json=row.rejected_counts_json,
                zone.source_credibility_status='unavailable',
                zone.metadata_scope='retained_history',
                zone.metadata_migrated_at=datetime($migrated_at),
                zone.scoring_version=$version,
                zone.status=CASE
                  WHEN zone.expires_at IS NOT NULL AND zone.expires_at > datetime()
                       AND zone.current_risk_score IS NOT NULL THEN 'available'
                  ELSE 'expired'
                END
            RETURN count(zone) AS updated
            """,
            {
                "rows": plan["zone_metadata"],
                "migrated_at": plan["generated_at"],
                "version": GDELT_SCORING_VERSION,
            },
        )
        zone_metadata_updates = int(rows[0]["updated"] if rows else 0)
    return {
        "event_updates": event_updates,
        "cluster_creates": cluster_creates,
        "membership_creates": membership_creates,
        "zone_metadata_updates": zone_metadata_updates,
    }


def write_artifact(plan: dict[str, Any], *, execute: bool, results: dict[str, int] | None) -> Path:
    artifact_dir = Path("artifacts") / "stage5_gdelt"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = artifact_dir / f"gdelt_migration_{timestamp}.json"
    payload = {
        key: value
        for key, value in plan.items()
        if key not in {"event_updates", "clusters", "memberships", "zone_metadata"}
    }
    payload.update(
        {
            "mode": "execute" if execute else "dry-run",
            "execution_results": results,
            "sample_event_updates": plan["event_updates"][:5],
            "sample_clusters": plan["clusters"][:5],
            "sample_memberships": plan["memberships"][:5],
            "zone_metadata": plan["zone_metadata"],
        }
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely classify and cluster existing GDELT events without deleting raw events"
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.execute and args.confirm != EXECUTE_CONFIRMATION:
        parser.error(f"--execute requires --confirm {EXECUTE_CONFIRMATION}")
    try:
        now = datetime.now(timezone.utc)
        events = read_events()
        plan = build_migration_plan(
            events,
            now=now,
            existing_cluster_ids=read_existing_cluster_ids(),
            existing_memberships=read_existing_memberships(),
        )
        results = execute_plan(plan) if args.execute else None
        artifact = write_artifact(plan, execute=args.execute, results=results)
        output = {
            key: value
            for key, value in plan.items()
            if key not in {"event_updates", "clusters", "memberships", "zone_metadata"}
        }
        output.update({"mode": "execute" if args.execute else "dry-run", "results": results, "artifact": str(artifact)})
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    finally:
        close_driver()


if __name__ == "__main__":
    raise SystemExit(main())
