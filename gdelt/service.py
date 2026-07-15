from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from gdelt.client import GdeltClient
from gdelt.config import GdeltSettings, load_zone_config
from gdelt.exposure import exposed_zone_ids
from gdelt.repository import apply_segment_overlay, ensure_schema, route_segments, write_zone
from gdelt.risk import parse_seen_date, score_zone


def update_news_risk(dry_run: bool = False, client: GdeltClient | None = None) -> dict[str, Any]:
    settings = GdeltSettings()
    config = load_zone_config()
    zones = config["zones"]
    gdelt_client = client or GdeltClient(settings)
    zone_results: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    for zone in zones:
        try:
            articles = gdelt_client.search(zone["query"])
            result = score_zone(articles)
            for article in result["articles"]:
                article["seen_at"] = parse_seen_date(article.get("seendate"))
            zone_results[zone["id"]] = result
        except Exception as exc:
            failures.append({"zoneId": zone["id"], "error": str(exc)})
    segments = route_segments()
    overlays = []
    for segment in segments:
        exposed = [zone_id for zone_id in exposed_zone_ids(segment, zones) if zone_id in zone_results]
        if exposed:
            overlays.append({"segmentId": segment["segment_id"], "zones": exposed})
    if not dry_run:
        ensure_schema()
        for zone in zones:
            if zone["id"] in zone_results:
                write_zone(zone, zone_results[zone["id"]], config["scoring_version"], settings.risk_ttl_hours)
        for segment in segments:
            exposed = [zone_id for zone_id in exposed_zone_ids(segment, zones) if zone_id in zone_results]
            apply_segment_overlay(segment, zone_results, exposed, settings.risk_ttl_hours)
    return {
        "updatedAt": datetime.now(timezone.utc).isoformat(), "dryRun": dry_run,
        "zonesUpdated": len(zone_results), "segmentsScanned": len(segments),
        "segmentsExposed": len(overlays), "overlays": overlays, "failures": failures,
        "zoneRisks": {zone_id: {key: value for key, value in result.items() if key != "articles"} | {"articleCount": len(result["articles"])} for zone_id, result in zone_results.items()},
    }
