#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ais.config import AisSettings, load_targets
from ais.service import AisConsumer
from database.neo4j_client import close_driver


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="Consume AISStream.io and aggregate port traffic")
    command.add_argument("--duration-seconds", type=int, help="Stop after N seconds; omit for a persistent worker")
    command.add_argument("--dry-run", action="store_true", help="Connect and aggregate without writing Neo4j")
    command.add_argument("--check-config", action="store_true", help="Validate configuration without network or database access")
    command.add_argument("--fixture", type=Path, help="Replay a JSONL fixture; requires --dry-run and never writes Neo4j")
    command.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return command


async def replay_fixture(consumer: AisConsumer, fixture_path: Path) -> dict:
    for line_number, line in enumerate(fixture_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            consumer.process_message(line)
        except Exception as exc:
            raise RuntimeError(f"Fixture line {line_number} failed: {exc}") from exc
    return consumer.flush()


async def run_worker(consumer: AisConsumer, duration_seconds: int | None) -> dict:
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(stop_signal, consumer.stop)
        except (NotImplementedError, RuntimeError):
            pass
    return await consumer.run(duration_seconds=duration_seconds)


def main() -> int:
    args = parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = AisSettings.from_environment()
    targets = load_targets(settings.target_config_path)
    consumer = AisConsumer(settings=settings, targets=targets, dry_run=args.dry_run)
    if args.check_config:
        print(json.dumps(consumer.public_configuration(), ensure_ascii=False, indent=2))
        return 0
    if args.fixture and not args.dry_run:
        print("--fixture requires --dry-run so fixture data can never be written to Neo4j", file=sys.stderr)
        return 2
    try:
        result = (
            asyncio.run(replay_fixture(consumer, args.fixture))
            if args.fixture
            else asyncio.run(run_worker(consumer, args.duration_seconds))
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logging.getLogger(__name__).error("AIS consumer stopped: %s", exc)
        return 1
    finally:
        close_driver()


if __name__ == "__main__":
    raise SystemExit(main())
