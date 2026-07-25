from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.vehicle_network.models import RouteGenerateRequest
from app.vehicle_network.repository import VehicleNetworkRepository
from app.vehicle_network.services import RouteGenerationService
from database.neo4j_client import run_query


def main() -> int:
    parser = argparse.ArgumentParser(description="审计并软删除地理上不可行的估算路线")
    parser.add_argument("--apply", action="store_true", help="实际执行软删除；不传时只预览")
    args = parser.parse_args()
    rows = run_query("""
        MATCH (route:VehicleRoute)-[:ORIGIN]->(origin)
        MATCH (route)-[:DESTINATION]->(destination)
        WHERE route.deleted_at IS NULL AND route.is_inferred=true
        RETURN route.route_id AS route_id,route.route_type AS mode,
               properties(origin) AS origin,labels(origin) AS origin_labels,
               properties(destination) AS destination,labels(destination) AS destination_labels
    """)
    service = RouteGenerationService()
    repository = VehicleNetworkRepository()
    invalid = []
    for row in rows:
        origin = row["origin"] | {"labels": row["origin_labels"]}
        destination = row["destination"] | {"labels": row["destination_labels"]}
        request = RouteGenerateRequest(origin="origin", destination="destination")
        feasible, rejected = service._mode_candidates(origin, destination, request)
        if row["mode"] not in feasible:
            reason = next((item["reason"] for item in rejected if item["mode"] == row["mode"]), "运输方式不可行")
            invalid.append({"route_id": row["route_id"], "mode": row["mode"], "reason": reason})
            if args.apply:
                repository.soft_delete_route(row["route_id"], "vehicle_route_feasibility_audit")
    print(json.dumps({"apply": args.apply, "checked": len(rows), "invalid_count": len(invalid), "invalid_routes": invalid}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
