from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gdelt.service import update_news_risk


def main() -> int:
    parser = argparse.ArgumentParser(description="Update GDELT maritime news risk and route overlays")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--zone-id", action="append", dest="zone_ids")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = update_news_risk(dry_run=args.dry_run, zone_ids=args.zone_ids)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["failures"] and result["zonesUpdated"] == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
