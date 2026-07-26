from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.neo4j_client import close_driver
from weather.route_service import update_route_weather


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sample Open-Meteo forecasts along route geometry or explicit endpoint fallback"
    )
    parser.add_argument("--segment-id", action="append", dest="segment_ids")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        result = update_route_weather(
            args.segment_ids,
            limit=args.limit,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["segmentsPlannedForWrite"] == 0 and result["errors"] else 0
    finally:
        close_driver()


if __name__ == "__main__":
    raise SystemExit(main())
