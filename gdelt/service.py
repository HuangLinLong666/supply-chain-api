from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from gdelt.client import GdeltClient
from gdelt.config import GdeltSettings, load_zone_config
from gdelt.exposure import exposed_zone_ids
from gdelt.repository import apply_segment_overlay, ensure_schema, route_segments, write_zone
from gdelt.risk import score_zone


LOGGER = logging.getLogger(__name__)


def update_news_risk(
    dry_run: bool = False,
    client: GdeltClient | None = None,
    zone_ids: list[str] | None = None,
) -> dict[str, Any]:
    settings = GdeltSettings()
    config = load_zone_config()
    zones = [zone for zone in config["zones"] if zone_ids is None or zone["id"] in zone_ids]
    gdelt_client = client or GdeltClient(settings)
    reference = datetime.now(timezone.utc)
    zone_results: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    for zone in zones:
        try:
            LOGGER.info("gdelt_zone_fetch_start zone_id=%s", zone["id"])
            articles = gdelt_client.search(zone["query"])
            result = score_zone(articles, now=reference, cluster_namespace=zone["id"])
            zone_results[zone["id"]] = result
            LOGGER.info(
                "gdelt_zone_fetch_complete zone_id=%s articles=%s clusters=%s status=%s",
                zone["id"],
                result["valid_article_count"],
                result["cluster_count"],
                result["status"],
            )
        except Exception as exc:
            LOGGER.warning("gdelt_zone_fetch_failed zone_id=%s error=%s", zone["id"], exc)
            failures.append({"zoneId": zone["id"], "error": str(exc)})
    segments = route_segments()
    overlays = []
    failed_zone_ids = {failure["zoneId"] for failure in failures}
    for segment in segments:
        inferred = exposed_zone_ids(segment, zones)
        exposed = [zone_id for zone_id in inferred if zone_id in zone_results]
        failed_exposed = sorted(set(inferred) & failed_zone_ids)
        if exposed:
            spatial_by_zone = {
                str(item.get("zone_id") or item.get("zoneId")): item
                for item in segment.get("spatial_exposures") or []
            }
            overlays.append(
                {
                    "segmentId": segment["segment_id"],
                    "zones": exposed,
                    "exposureEvidence": [spatial_by_zone[zone_id] for zone_id in exposed if zone_id in spatial_by_zone],
                    "activeZones": [zone_id for zone_id in exposed if zone_results[zone_id]["status"] == "available"],
                    "partialBecauseFetchFailed": bool(failed_exposed),
                    "failedZoneIds": failed_exposed,
                }
            )
    if not dry_run:
        ensure_schema()
        for zone in zones:
            if zone["id"] in zone_results:
                write_zone(zone, zone_results[zone["id"]], config["scoring_version"], settings.risk_ttl_hours)
        for segment in segments:
            inferred = exposed_zone_ids(segment, zones)
            failed_exposed = set(inferred) & failed_zone_ids
            exposed = [zone_id for zone_id in inferred if zone_id in zone_results]
            if failed_exposed and not exposed:
                continue
            apply_segment_overlay(segment, zone_results, exposed, settings.risk_ttl_hours)
    return {
        "updatedAt": datetime.now(timezone.utc).isoformat(), "dryRun": dry_run,
        "zonesRequested": len(zones), "zonesUpdated": len(zone_results), "segmentsScanned": len(segments),
        "segmentsExposed": len(overlays), "overlays": overlays, "failures": failures,
        "zoneRisks": {
            zone_id: {
                key: value
                for key, value in result.items()
                if key not in {"articles", "clusters"}
            }
            | {
                "articleCount": len(result["articles"]),
                "clusterCount": len(result["clusters"]),
            }
            for zone_id, result in zone_results.items()
        },
    }
