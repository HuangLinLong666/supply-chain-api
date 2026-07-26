#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ais.config import AisSettings, load_targets
from ais.repository import AisRepository
from database.neo4j_client import close_driver


CONFIRMATION = "APPLY_AIS_STAGE7"


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Prepare Stage 7 AIS schema and observation targets")
    command.add_argument("--execute", action="store_true", help="Apply additive schema and target changes")
    command.add_argument("--confirm", help=f"Required with --execute: {CONFIRMATION}")
    return command


def main() -> int:
    args = parser().parse_args()
    settings = AisSettings.from_environment()
    targets = load_targets(settings.target_config_path)
    repository = AisRepository()
    if not args.execute:
        print(
            json.dumps(
                {
                    "dryRun": True,
                    "configured": settings.configured,
                    "targetCount": len(targets),
                    "targets": [target.public_dict() for target in targets],
                    "plannedChanges": [
                        "create additive AIS constraints and indexes",
                        "MERGE four AisObservationTarget nodes",
                        "link targets to existing Port or GeoZone nodes",
                        "mark explicit legacy DEMO vessel observations synthetic and excluded",
                        "write provider state without storing or displaying the API key",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.confirm != CONFIRMATION:
        print(f"Refusing write. Pass --confirm {CONFIRMATION}", file=sys.stderr)
        return 2
    try:
        repository.ensure_schema()
        repository.sync_targets(targets)
        excluded = repository.mark_legacy_demo_observations()
        repository.update_provider_state(
            status="configured_not_running" if settings.configured else "unavailable_missing_api_key",
            configured=settings.configured,
            connected=False,
            migration="stage7",
        )
        summary = repository.migration_summary()
        print(
            json.dumps(
                {
                    "dryRun": False,
                    "configured": settings.configured,
                    "legacyDemoExcluded": excluded,
                    "database": summary,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0
    finally:
        close_driver()


if __name__ == "__main__":
    raise SystemExit(main())
