from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from database.neo4j_client import run_query


def ensure_schema() -> None:
    for statement in (
        "CREATE CONSTRAINT news_risk_zone_id IF NOT EXISTS FOR (z:NewsRiskZone) REQUIRE z.zone_id IS UNIQUE",
        "CREATE CONSTRAINT news_risk_event_id IF NOT EXISTS FOR (e:NewsRiskEvent) REQUIRE e.article_id IS UNIQUE",
        "CREATE INDEX news_risk_event_seen IF NOT EXISTS FOR (e:NewsRiskEvent) ON (e.seen_at)",
    ):
        run_query(statement)


def route_segments() -> list[dict[str, Any]]:
    return run_query("""
        MATCH (s:RouteSegment)-[:FROM_NODE]->(a)
        MATCH (s)-[:TO_NODE]->(b)
        RETURN elementId(s) AS element_id,
          coalesce(s.segmentId,s.segment_id,elementId(s)) AS segment_id,
          coalesce(s.mode,s.routeMode) AS mode,
          coalesce(a.name,a.code,a.id,s.fromNodeName) AS from_name,a.city AS from_city,a.country AS from_country,
          a.latitude AS from_lat,a.longitude AS from_lng,
          coalesce(b.name,b.code,b.id,s.toNodeName) AS to_name,b.city AS to_city,b.country AS to_country,
          b.latitude AS to_lat,b.longitude AS to_lng,
          coalesce(s.total_risk_score,s.riskScore,s.base_risk_score,0.5) AS base_risk
    """)


def write_zone(zone: dict[str, Any], result: dict[str, Any], version: str, ttl_hours: int) -> None:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=ttl_hours)
    run_query("""
        MERGE (z:NewsRiskZone {zone_id:$zone_id})
        SET z.name=$name,z.zone_type=$zone_type,z.query=$query,z.current_risk_score=$score,
            z.current_risk_level=$level,z.confidence=$confidence,z.article_count=$article_count,
            z.updated_at=datetime($updated_at),z.expires_at=datetime($expires_at),z.scoring_version=$version,
            z.source='GDELT DOC 2.0 API'
    """, {"zone_id": zone["id"], "name": zone["name"], "zone_type": zone["type"], "query": zone["query"],
           "score": result["score"], "level": result["level"], "confidence": result["confidence"],
           "article_count": len(result["articles"]), "updated_at": now.isoformat(), "expires_at": expires_at.isoformat(),
           "version": version})
    for article in result["articles"]:
        run_query("""
            MATCH (z:NewsRiskZone {zone_id:$zone_id})
            MERGE (e:NewsRiskEvent {article_id:$article_id})
            SET e.title=$title,e.url=$url,e.domain=$domain,e.language=$language,e.source_country=$source_country,
                e.seen_at=datetime($seen_at),e.severity=$severity,e.matched_terms=$matched_terms,e.source='GDELT DOC 2.0 API'
            MERGE (e)-[:AFFECTS_ZONE]->(z)
        """, {"zone_id": zone["id"], "article_id": article["article_id"], "title": article.get("title"),
               "url": article.get("url"), "domain": article.get("domain"), "language": article.get("language"),
               "source_country": article.get("sourcecountry"), "seen_at": article["seen_at"],
               "severity": article["severity"], "matched_terms": article["matched_terms"]})


def apply_segment_overlay(segment: dict[str, Any], zone_results: dict[str, dict[str, Any]], exposed: list[str], ttl_hours: int) -> None:
    active_scores = [zone_results[zone_id]["score"] for zone_id in exposed]
    news_risk = max(active_scores, default=0.0)
    base_risk = float(segment.get("base_risk") or 0.5)
    effective_risk = 1 - (1 - base_risk) * (1 - news_risk)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    run_query("""
        MATCH (s:RouteSegment) WHERE elementId(s)=$element_id
        SET s.news_risk_score=$news_risk,s.dynamic_risk_score=$effective_risk,
            s.news_risk_zones=$zones,s.news_risk_updated_at=datetime(),s.news_risk_expires_at=datetime($expires_at)
        WITH s
        OPTIONAL MATCH (s)-[old:EXPOSED_TO_NEWS_RISK]->(:NewsRiskZone) DELETE old
        WITH s
        UNWIND $zones AS zone_id
        MATCH (z:NewsRiskZone {zone_id:zone_id})
        MERGE (s)-[:EXPOSED_TO_NEWS_RISK]->(z)
    """, {"element_id": segment["element_id"], "news_risk": news_risk, "effective_risk": round(effective_risk, 4),
           "zones": exposed, "expires_at": expires_at.isoformat()})
