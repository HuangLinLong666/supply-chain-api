from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.provider_risk import build_segment_signals, calculate_provider_risk, database_risk_properties, is_fresh, parse_datetime
from app.vehicle_network.core import load_strategy
from database.neo4j_client import run_query
from database.unified_schema import IDENTITY_CONSTRAINTS, QUERY_INDEXES, SCHEMA_VERSION


def ensure_schema() -> None:
    unified = [
        rule.statement
        for rule in (*IDENTITY_CONSTRAINTS, *QUERY_INDEXES)
        if rule.label in {"GeoZone", "RiskObservation", "Evidence"}
    ]
    for statement in (
        "CREATE CONSTRAINT news_risk_zone_id IF NOT EXISTS FOR (z:NewsRiskZone) REQUIRE z.zone_id IS UNIQUE",
        "CREATE CONSTRAINT news_risk_event_id IF NOT EXISTS FOR (e:NewsRiskEvent) REQUIRE e.article_id IS UNIQUE",
        "CREATE CONSTRAINT news_risk_cluster_id IF NOT EXISTS FOR (c:NewsRiskCluster) REQUIRE c.cluster_id IS UNIQUE",
        "CREATE INDEX news_risk_event_seen IF NOT EXISTS FOR (e:NewsRiskEvent) ON (e.seen_at)",
        "CREATE INDEX news_risk_event_category IF NOT EXISTS FOR (e:NewsRiskEvent) ON (e.event_category)",
        "CREATE INDEX news_risk_event_canonical_url IF NOT EXISTS FOR (e:NewsRiskEvent) ON (e.canonical_url_hash)",
        "CREATE INDEX news_risk_cluster_category IF NOT EXISTS FOR (c:NewsRiskCluster) ON (c.event_category)",
        "CREATE INDEX news_risk_cluster_last_seen IF NOT EXISTS FOR (c:NewsRiskCluster) ON (c.last_seen)",
        *unified,
    ):
        run_query(statement)


def route_segments() -> list[dict[str, Any]]:
    return run_query("""
        MATCH (s:RouteSegment)-[:FROM_NODE]->(a)
        MATCH (s)-[:TO_NODE]->(b)
        WHERE coalesce(s.feasibility_status,'') <> 'invalid_cross_ocean'
        OPTIONAL MATCH (s)-[spatial:PASSES_THROUGH]->(spatial_zone:GeoZone)
        WHERE coalesce(spatial.active,true)=true
        WITH s,a,b,
          [item IN collect(DISTINCT CASE WHEN spatial_zone IS NULL THEN null ELSE {
            zone_id:spatial_zone.zone_id,zone_name:spatial_zone.name,
            exposure_method:spatial.exposure_method,confidence:spatial.confidence,
            exposure_ratio:spatial.exposure_ratio,active:coalesce(spatial.active,true)
          } END) WHERE item IS NOT NULL] AS spatial_exposures
        RETURN elementId(s) AS element_id,
          coalesce(s.segmentId,s.segment_id,elementId(s)) AS segment_id,
          coalesce(s.mode,s.routeMode) AS mode,
          coalesce(a.name,a.code,a.id,s.fromNodeName) AS from_name,a.city AS from_city,a.country AS from_country,
          a.latitude AS from_lat,a.longitude AS from_lng,
          coalesce(b.name,b.code,b.id,s.toNodeName) AS to_name,b.city AS to_city,b.country AS to_country,
          b.latitude AS to_lat,b.longitude AS to_lng,
          s.geospatial_version AS geospatial_version,
          s.geometry_geojson AS geometry_geojson,
          s.geometry_status AS geometry_status,
          s.geometry_confidence AS geometry_confidence,
          spatial_exposures,
          s.route_weather_risk AS route_weather_risk,
          s.route_weather_provider AS route_weather_provider,
          s.route_weather_updated_at AS route_weather_updated_at,
          s.route_weather_expires_at AS route_weather_expires_at,
          s.route_weather_status AS route_weather_status,
          s.route_weather_data_completeness AS route_weather_data_completeness,
          s.route_weather_confidence AS route_weather_confidence,
          s.route_weather_evidence AS route_weather_evidence,
          s.ais_congestion_score AS ais_congestion_score,
          s.ais_congestion_provider AS ais_congestion_provider,
          s.ais_congestion_status AS ais_congestion_status,
          s.ais_congestion_confidence AS ais_congestion_confidence,
          s.ais_congestion_data_completeness AS ais_congestion_data_completeness,
          s.ais_congestion_observed_at AS ais_congestion_observed_at,
          s.ais_congestion_expires_at AS ais_congestion_expires_at,
          s.ais_congestion_snapshot_ids AS ais_congestion_snapshot_ids
    """)


def write_zone(zone: dict[str, Any], result: dict[str, Any], version: str, ttl_hours: int) -> None:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=ttl_hours)
    run_query("""
        MERGE (z:NewsRiskZone {zone_id:$zone_id})
        SET z:GeoZone,z.name=$name,z.zone_type=$zone_type,z.query=$query,z.current_risk_score=$score,
            z.current_risk_level=$level,z.confidence=$confidence,z.article_count=$article_count,
            z.raw_article_count=$raw_article_count,z.valid_article_count=$valid_article_count,
            z.event_cluster_count=$cluster_count,z.category_counts_json=$category_counts_json,
            z.rejected_counts_json=$rejected_counts_json,z.status=$status,
            z.updated_at=datetime($updated_at),z.expires_at=datetime($expires_at),z.scoring_version=$version,
            z.source='GDELT DOC 2.0 API',z.source_type='open_api',z.provider='GDELT',
            z.data_status='observed',z.source_credibility_status=$source_credibility_status,
            z.metadata_scope='current_fetch_window',
            z.schema_version=$schema_version
    """, {"zone_id": zone["id"], "name": zone["name"], "zone_type": zone["type"], "query": zone["query"],
           "score": result["score"], "level": result["level"], "confidence": result["confidence"],
           "article_count": len(result["articles"]), "raw_article_count": result["raw_article_count"],
           "valid_article_count": result["valid_article_count"], "cluster_count": result["cluster_count"],
           "category_counts_json": json.dumps(result["category_counts"], ensure_ascii=False, sort_keys=True),
           "rejected_counts_json": json.dumps(result["rejected_counts"], ensure_ascii=False, sort_keys=True),
           "status": result["status"], "source_credibility_status": result["source_credibility_status"],
           "updated_at": now.isoformat(), "expires_at": expires_at.isoformat(),
           "version": version, "schema_version": SCHEMA_VERSION})
    article_rows = [
        {
            "article_id": article["article_id"],
            "raw_title": article.get("raw_title"),
            "raw_url": article.get("raw_url"),
            "title": article.get("title"),
            "url": article.get("url"),
            "canonical_url": article.get("canonical_url"),
            "canonical_url_hash": article.get("canonical_url_hash"),
            "normalized_title": article.get("normalized_title"),
            "content_hash": article.get("content_hash"),
            "domain": article.get("domain"),
            "language": article.get("language"),
            "source_country": article.get("sourcecountry") or article.get("source_country"),
            "seen_at": article["seen_at"],
            "severity": article["severity"],
            "matched_terms": article["matched_terms"],
            "matched_categories": article["matched_categories"],
            "event_category": article["event_category"],
            "classification_status": article["classification_status"],
            "time_status": article["time_status"],
        }
        for article in result["articles"]
    ]
    if article_rows:
        run_query(
            """
            MATCH (z:NewsRiskZone {zone_id:$zone_id})
            UNWIND $articles AS row
            MERGE (e:NewsRiskEvent {article_id:row.article_id})
            SET e:RiskObservation:Evidence,e.title=row.title,e.url=row.url,
                e.raw_title=coalesce(e.raw_title,row.raw_title),e.raw_url=coalesce(e.raw_url,row.raw_url),
                e.canonical_url=row.canonical_url,e.canonical_url_hash=row.canonical_url_hash,
                e.normalized_title=row.normalized_title,e.content_hash=row.content_hash,
                e.domain=row.domain,e.language=row.language,e.source_country=row.source_country,
                e.seen_at=datetime(row.seen_at),e.observed_at=datetime(row.seen_at),
                e.collected_at=datetime($collected_at),e.severity=row.severity,
                e.matched_terms=row.matched_terms,e.matched_categories=row.matched_categories,
                e.event_category=row.event_category,e.classification_status=row.classification_status,
                e.time_status=row.time_status,e.source='GDELT DOC 2.0 API',
                e.source_url=coalesce(row.canonical_url,row.url),e.source_type='open_api',
                e.provider='GDELT',e.data_status='observed',e.status='available',
                e.source_credibility_status='unavailable',e.observation_id=row.article_id,
                e.evidence_id=row.article_id,e.observation_type='news_event_risk',
                e.evidence_type='news_article',e.scoring_version=$version,
                e.schema_version=$schema_version
            MERGE (e)-[:AFFECTS_ZONE]->(z)
            """,
            {
                "zone_id": zone["id"],
                "articles": article_rows,
                "collected_at": now.isoformat(),
                "version": version,
                "schema_version": SCHEMA_VERSION,
            },
        )
        run_query(
            """
            MATCH (zone:NewsRiskZone {zone_id:$zone_id})
            UNWIND $article_ids AS article_id
            MATCH (event:NewsRiskEvent {article_id:article_id})
            OPTIONAL MATCH (event)-[membership:MEMBER_OF_EVENT_CLUSTER]->(old_cluster:NewsRiskCluster)-[:AFFECTS_ZONE]->(zone)
            DELETE membership
            """,
            {
                "zone_id": zone["id"],
                "article_ids": [row["article_id"] for row in article_rows],
            },
        )
    cluster_rows = [
        {
            key: cluster.get(key)
            for key in (
                "cluster_id",
                "event_category",
                "representative_title",
                "representative_article_id",
                "first_seen",
                "last_seen",
                "severity",
                "freshness_weight",
                "effective_severity",
                "article_count",
                "distinct_domain_count",
                "domains",
                "article_ids",
                "source_credibility_status",
                "confidence_basis",
            )
        }
        for cluster in result["clusters"]
    ]
    if cluster_rows:
        run_query(
            """
            MATCH (z:NewsRiskZone {zone_id:$zone_id})
            UNWIND $clusters AS row
            MERGE (cluster:NewsRiskCluster {cluster_id:row.cluster_id})
            SET cluster:RiskObservation,cluster.event_category=row.event_category,
                cluster.representative_title=row.representative_title,
                cluster.representative_article_id=row.representative_article_id,
                cluster.first_seen=datetime(row.first_seen),cluster.last_seen=datetime(row.last_seen),
                cluster.observed_at=datetime(row.last_seen),cluster.collected_at=datetime($collected_at),
                cluster.expires_at=datetime($expires_at),cluster.severity=row.severity,
                cluster.freshness_weight=row.freshness_weight,
                cluster.effective_severity=row.effective_severity,
                cluster.article_count=row.article_count,
                cluster.distinct_domain_count=row.distinct_domain_count,cluster.domains=row.domains,
                cluster.provider='GDELT',cluster.source='GDELT DOC 2.0 API event cluster',
                cluster.source_type='open_api',cluster.data_status='estimated',cluster.is_inferred=true,
                cluster.calculation_status='derived_from_observed_events',
                cluster.status='available',cluster.source_credibility_status=row.source_credibility_status,
                cluster.confidence_basis=row.confidence_basis,
                cluster.observation_id=row.cluster_id,cluster.observation_type='news_event_cluster',
                cluster.scoring_version=$version,cluster.schema_version=$schema_version
            MERGE (cluster)-[:AFFECTS_ZONE]->(z)
            WITH z,row,cluster
            UNWIND row.article_ids AS article_id
            MATCH (event:NewsRiskEvent {article_id:article_id})
            MERGE (event)-[membership:MEMBER_OF_EVENT_CLUSTER]->(cluster)
            SET membership.zone_id=$zone_id,membership.scoring_version=$version
            """,
            {
                "zone_id": zone["id"],
                "clusters": cluster_rows,
                "collected_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
                "version": version,
                "schema_version": SCHEMA_VERSION,
            },
        )


def apply_segment_overlay(segment: dict[str, Any], zone_results: dict[str, dict[str, Any]], exposed: list[str], ttl_hours: int) -> None:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=ttl_hours)
    active_results = [
        (zone_id, zone_results[zone_id])
        for zone_id in exposed
        if zone_results[zone_id].get("score") is not None
        and zone_results[zone_id].get("status", "available") == "available"
    ]
    highest = max(active_results, key=lambda item: float(item[1]["score"]), default=None)
    news_risk = highest[1]["score"] if highest else None
    news_confidence = highest[1].get("confidence") if highest else None
    active_zone_ids = [zone_id for zone_id, _ in active_results]
    cluster_ids = sorted(
        {
            str(cluster["cluster_id"])
            for _, result in active_results
            for cluster in result.get("clusters") or []
            if cluster.get("cluster_id") and cluster.get("event_category") != "other"
        }
    )
    cluster_observed_at = max(
        (
            str(cluster["last_seen"])
            for _, result in active_results
            for cluster in result.get("clusters") or []
            if cluster.get("last_seen") and cluster.get("event_category") != "other"
        ),
        default=now.isoformat(),
    )
    weather_updated_at = segment.get("route_weather_updated_at")
    weather_expires_at = parse_datetime(segment.get("route_weather_expires_at"))
    weather_active = bool(segment.get("route_weather_provider")) and (
        weather_expires_at > now
        if weather_expires_at is not None
        else is_fresh(weather_updated_at, now=now, max_age_hours=6)
    )
    weather_observed_at = parse_datetime(weather_updated_at)
    if weather_expires_at is None:
        weather_expires_at = weather_observed_at + timedelta(hours=6) if weather_observed_at else None
    congestion_expires_at = parse_datetime(segment.get("ais_congestion_expires_at"))
    congestion_evidence = segment.get("ais_congestion_snapshot_ids") or []
    congestion_active = (
        str(segment.get("ais_congestion_provider") or "").casefold() in {"aisstream", "aisstream.io"}
        and segment.get("ais_congestion_status") == "available"
        and congestion_expires_at is not None
        and congestion_expires_at > now
        and bool(congestion_evidence)
    )
    signals = build_segment_signals(
        segment.get("mode"),
        news_score=news_risk,
        news_provider="GDELT" if highest else None,
        news_observed_at=cluster_observed_at if highest else None,
        news_expires_at=expires_at.isoformat() if highest else None,
        news_confidence=news_confidence,
        news_evidence=cluster_ids,
        weather_score=segment.get("route_weather_risk") if weather_active else None,
        weather_provider=segment.get("route_weather_provider") if weather_active else None,
        weather_observed_at=weather_updated_at if weather_active else None,
        weather_expires_at=weather_expires_at.isoformat() if weather_active and weather_expires_at else None,
        weather_confidence=segment.get("route_weather_confidence") if weather_active else None,
        weather_evidence=segment.get("route_weather_evidence") or [],
        congestion_score=segment.get("ais_congestion_score") if congestion_active else None,
        congestion_provider="AISStream.io" if congestion_active else None,
        congestion_observed_at=segment.get("ais_congestion_observed_at") if congestion_active else None,
        congestion_expires_at=segment.get("ais_congestion_expires_at") if congestion_active else None,
        congestion_confidence=segment.get("ais_congestion_confidence") if congestion_active else None,
        congestion_evidence=congestion_evidence,
    )
    risk = calculate_provider_risk(segment.get("mode"), signals, load_strategy())
    risk_properties = database_risk_properties(risk, now)
    run_query("""
        MATCH (s:RouteSegment) WHERE elementId(s)=$element_id
        REMOVE s.riskScore,s.base_risk_score,s.costRiskScore,s.supplier_risk,s.comprehensive_risk_score
        SET s.news_risk_score=$news_risk,s.news_risk_provider=$news_provider,
            s.news_risk_confidence=$news_confidence,s.news_risk_zones=$active_zones,
            s.news_risk_cluster_ids=$cluster_ids,
            s.news_risk_status=CASE WHEN $news_provider IS NULL THEN 'unavailable' ELSE 'available' END,
            s.news_risk_updated_at=CASE WHEN $news_provider IS NULL THEN null ELSE datetime($updated_at) END,
            s.news_risk_expires_at=CASE WHEN $news_provider IS NULL THEN null ELSE datetime($expires_at) END,
            s += $risk_properties,
            s.risk_recalculated_at=datetime($updated_at)
        WITH s
        OPTIONAL MATCH (s)-[old:EXPOSED_TO_NEWS_RISK]->(:NewsRiskZone) DELETE old
        WITH s
        UNWIND $exposed_zones AS zone_id
        MATCH (z:NewsRiskZone {zone_id:zone_id})
        MERGE (s)-[:EXPOSED_TO_NEWS_RISK]->(z)
    """, {
        "element_id": segment["element_id"],
        "news_risk": news_risk,
        "news_provider": "GDELT" if highest else None,
        "news_confidence": news_confidence,
        "active_zones": active_zone_ids,
        "exposed_zones": exposed,
        "cluster_ids": cluster_ids,
        "updated_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "risk_properties": risk_properties,
    })
    run_query(
        """
        MATCH (segment:RouteSegment) WHERE elementId(segment)=$element_id
        OPTIONAL MATCH (segment)-[old:EXPOSED_TO_NEWS_CLUSTER]->(:NewsRiskCluster)
        DELETE old
        WITH segment
        UNWIND $cluster_ids AS cluster_id
        MATCH (cluster:NewsRiskCluster {cluster_id:cluster_id})
        MERGE (segment)-[:EXPOSED_TO_NEWS_CLUSTER]->(cluster)
        """,
        {"element_id": segment["element_id"], "cluster_ids": cluster_ids},
    )
