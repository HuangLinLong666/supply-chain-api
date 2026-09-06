"""Read-only audit for real mode coverage and Hormuz rerouting data."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.neo4j_client import close_driver, run_query


def audit(query=run_query) -> dict:
    zones = query(
        """
        MATCH (zone)
        WHERE (zone:GeoZone OR zone:NewsRiskZone)
          AND (toLower(coalesce(zone.zone_id,'')) IN ['zone-hormuz','hormuz-strait']
               OR toLower(coalesce(zone.name,'')) CONTAINS 'hormuz'
               OR coalesce(zone.name,'') CONTAINS '霍尔木兹')
        RETURN coalesce(zone.zone_id,elementId(zone)) AS zoneId,zone.name AS name,labels(zone) AS labels
        """
    )
    exposures = query(
        """
        MATCH (segment:RouteSegment)-[exposure:PASSES_THROUGH]->(zone)
        WHERE (toLower(coalesce(zone.zone_id,'')) IN ['zone-hormuz','hormuz-strait']
               OR toLower(coalesce(zone.name,'')) CONTAINS 'hormuz'
               OR coalesce(zone.name,'') CONTAINS '霍尔木兹')
          AND coalesce(exposure.active,true)=true
        MATCH (segment)-[:FROM_NODE]->(origin:TransportLocation)
        MATCH (segment)-[:TO_NODE]->(destination:TransportLocation)
        RETURN coalesce(segment.segment_id,segment.segmentId,elementId(segment)) AS segmentId,
               coalesce(segment.canonical_mode,segment.mode,segment.routeMode) AS mode,
               coalesce(segment.data_status,'unknown') AS dataStatus,
               coalesce(segment.source,segment.provider,'unavailable') AS source,
               elementId(origin) AS fromId,elementId(destination) AS toId
        ORDER BY segmentId
        """
    )
    mode_counts = query(
        """
        MATCH (segment:RouteSegment)
        WHERE coalesce(segment.feasibility_status,'') <> 'invalid_cross_ocean'
          AND coalesce(segment.data_status,'') <> 'synthetic'
        RETURN coalesce(segment.canonical_mode,segment.mode,segment.routeMode,'unknown') AS mode,
               count(*) AS candidateSegmentCount
        ORDER BY mode
        """
    )
    real_segments = query(
        """
        MATCH (segment:RouteSegment)-[:FROM_NODE]->(origin:TransportLocation)
        MATCH (segment)-[:TO_NODE]->(destination:TransportLocation)
        WHERE coalesce(segment.feasibility_status,'') <> 'invalid_cross_ocean'
          AND coalesce(segment.data_status,'') <> 'synthetic'
          AND NOT EXISTS {
            MATCH (segment)-[exposure:PASSES_THROUGH]->(zone)
            WHERE coalesce(exposure.active,true)=true
              AND (toLower(coalesce(zone.zone_id,'')) IN ['zone-hormuz','hormuz-strait']
                   OR toLower(coalesce(zone.name,'')) CONTAINS 'hormuz'
                   OR coalesce(zone.name,'') CONTAINS '霍尔木兹')
          }
        RETURN coalesce(segment.segment_id,segment.segmentId,elementId(segment)) AS segmentId,
               elementId(origin) AS fromId,elementId(destination) AS toId,
               coalesce(segment.canonical_mode,segment.mode,segment.routeMode) AS mode,
               coalesce(segment.data_status,'unknown') AS dataStatus
        """
    )
    adjacency = {}
    for segment in real_segments:
        adjacency.setdefault(str(segment["fromId"]), []).append(segment)
    alternatives = []
    for exposure in exposures:
        origin = str(exposure.get("fromId") or "")
        destination = str(exposure.get("toId") or "")
        queue = [(origin, [])]
        visited = {origin}
        found = None
        while queue:
            node, path = queue.pop(0)
            if node == destination and path:
                found = path
                break
            if len(path) >= 20:
                continue
            for segment in adjacency.get(node, []):
                next_id = str(segment["toId"])
                if next_id in visited:
                    continue
                visited.add(next_id)
                queue.append((next_id, [*path, segment]))
        if found:
            alternatives.append({
                "replacesSegmentId": exposure["segmentId"],
                "fromId": origin,
                "toId": destination,
                "segmentIds": [segment["segmentId"] for segment in found],
                "modes": list(dict.fromkeys(str(segment["mode"]) for segment in found)),
                "dataStatuses": list(dict.fromkeys(str(segment["dataStatus"]) for segment in found)),
            })
    blockers = []
    if not zones:
        blockers.append("hormuz_zone_missing")
    if not exposures:
        blockers.append("hormuz_passes_through_missing")
    if not alternatives:
        blockers.append("real_alternative_route_missing")
    return {
        "auditVersion": "route-mode-rerouting-audit-v1",
        "readOnly": True,
        "syntheticRoutesCreated": 0,
        "hormuzZones": zones,
        "hormuzExposures": exposures,
        "modeCandidateCounts": mode_counts,
        "realAlternatives": alternatives,
        "rerouteBlockers": blockers,
        "passed": not blockers,
    }


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--summary", action="store_true", help="print only counts, alternatives, and blockers")
    args = parser.parse_args()
    try:
        report = audit()
        output = report
        if args.summary:
            output = {
                "auditVersion": report["auditVersion"],
                "readOnly": report["readOnly"],
                "syntheticRoutesCreated": report["syntheticRoutesCreated"],
                "hormuzZoneCount": len(report["hormuzZones"]),
                "hormuzExposureCount": len(report["hormuzExposures"]),
                "hormuzExposures": report["hormuzExposures"],
                "modeCandidateCounts": report["modeCandidateCounts"],
                "realAlternativeCount": len(report["realAlternatives"]),
                "realAlternatives": report["realAlternatives"],
                "rerouteBlockers": report["rerouteBlockers"],
                "passed": report["passed"],
            }
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        raise SystemExit(0 if report["passed"] else 2)
    finally:
        close_driver()
