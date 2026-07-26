from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import socket
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ais.aggregation import PortTrafficAggregator
from ais.config import AisSettings, AisTarget, load_targets
from ais.models import NormalizedAisObservation, merge_observations, utc_now
from ais.parser import AisMessageError, AisProviderMessageError, parse_ais_message
from ais.repository import AisRepository
from ais.storage import AisStorage


logger = logging.getLogger(__name__)

AIS_MESSAGE_TYPES = [
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "LongRangeAisBroadcastMessage",
    "ShipStaticData",
    "StaticDataReport",
]


class DedupeCache:
    def __init__(self, *, ttl_seconds: int, max_entries: int) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: OrderedDict[str, float] = OrderedDict()

    def seen(self, key: str, now: float | None = None) -> bool:
        current = now if now is not None else time.monotonic()
        cutoff = current - self.ttl_seconds
        while self._entries and next(iter(self._entries.values())) < cutoff:
            self._entries.popitem(last=False)
        if key in self._entries:
            self._entries.move_to_end(key)
            self._entries[key] = current
            return True
        self._entries[key] = current
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        return False


@dataclass
class ConsumerCounters:
    messages_received: int = 0
    messages_parsed: int = 0
    messages_ignored: int = 0
    duplicates_ignored: int = 0
    malformed_messages: int = 0
    reconnect_count: int = 0
    observations_written: int = 0
    snapshots_written: int = 0
    segments_updated: int = 0

    def public_dict(self) -> dict[str, int]:
        return {
            "messagesReceived": self.messages_received,
            "messagesParsed": self.messages_parsed,
            "messagesIgnored": self.messages_ignored,
            "duplicatesIgnored": self.duplicates_ignored,
            "malformedMessages": self.malformed_messages,
            "reconnectCount": self.reconnect_count,
            "observationsWritten": self.observations_written,
            "snapshotsWritten": self.snapshots_written,
            "segmentsUpdated": self.segments_updated,
        }


class AisConsumer:
    def __init__(
        self,
        settings: AisSettings | None = None,
        targets: list[AisTarget] | None = None,
        storage: AisStorage | None = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self.settings = settings or AisSettings.from_environment()
        self.targets = targets or load_targets(self.settings.target_config_path)
        self.storage = storage or AisRepository()
        self.dry_run = dry_run
        self.aggregator = PortTrafficAggregator(
            self.targets,
            window_minutes=self.settings.window_minutes,
            snapshot_ttl_minutes=self.settings.snapshot_ttl_minutes,
        )
        self.dedupe = DedupeCache(
            ttl_seconds=self.settings.dedupe_ttl_seconds,
            max_entries=self.settings.dedupe_max_entries,
        )
        self.pending: dict[str, NormalizedAisObservation] = {}
        self.counters = ConsumerCounters()
        self.last_message_at: datetime | None = None
        self.last_snapshot_at: datetime | None = None
        self._stop_event = asyncio.Event()
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"

    def subscription_message(self) -> dict[str, Any]:
        return {
            "APIKey": self.settings.require_api_key(),
            "BoundingBoxes": [target.subscription_bbox.as_subscription_box() for target in self.targets],
            "FilterMessageTypes": AIS_MESSAGE_TYPES,
        }

    def public_configuration(self) -> dict[str, Any]:
        return {
            **self.settings.public_dict(),
            "targets": [target.public_dict() for target in self.targets],
            "messageTypes": AIS_MESSAGE_TYPES,
            "apiKeyExposed": False,
        }

    def stop(self) -> None:
        self._stop_event.set()

    def process_message(self, payload: str | bytes | dict[str, Any], received_at: datetime | None = None) -> bool:
        self.counters.messages_received += 1
        try:
            observation = parse_ais_message(payload, received_at=received_at)
        except AisProviderMessageError:
            raise
        except AisMessageError as exc:
            self.counters.malformed_messages += 1
            logger.warning("Ignoring malformed AIS message: %s", exc)
            return False
        if observation is None:
            self.counters.messages_ignored += 1
            return False
        if self.dedupe.seen(observation.dedupe_key):
            self.counters.duplicates_ignored += 1
            return False
        target_ids = self.aggregator.ingest(observation)
        if target_ids:
            observation = observation.with_targets(target_ids)
        self.pending[observation.mmsi] = merge_observations(self.pending.get(observation.mmsi), observation)
        self.counters.messages_parsed += 1
        self.last_message_at = observation.received_at
        return True

    def flush(self, now: datetime | None = None) -> dict[str, Any]:
        current = (now or utc_now()).astimezone(timezone.utc)
        observations = list(self.pending.values())
        snapshots = self.aggregator.build_snapshots(current)
        if self.dry_run:
            self.pending.clear()
            if snapshots:
                self.last_snapshot_at = max(snapshot.observed_at for snapshot in snapshots)
            return {
                "dryRun": True,
                "observations": len(observations),
                "snapshots": [snapshot.storage_row() for snapshot in snapshots],
                "counters": self.counters.public_dict(),
            }
        observations_written = self.storage.upsert_latest_observations(observations)
        snapshot_result = self.storage.write_snapshots(snapshots)
        self.pending.clear()
        self.counters.observations_written += observations_written
        self.counters.snapshots_written += int(snapshot_result.get("snapshots") or 0)
        self.counters.segments_updated += int(snapshot_result.get("segments") or 0)
        if snapshots:
            self.last_snapshot_at = max(snapshot.observed_at for snapshot in snapshots)
        try:
            self.storage.update_provider_state(
                status="connected",
                configured=True,
                connected=True,
                worker_id=self.worker_id,
                last_message_at=self.last_message_at.isoformat() if self.last_message_at else None,
                last_snapshot_at=self.last_snapshot_at.isoformat() if self.last_snapshot_at else None,
                last_successful_flush_at=current.isoformat(),
                messages_received=self.counters.messages_received,
                messages_parsed=self.counters.messages_parsed,
                messages_ignored=self.counters.messages_ignored,
                duplicates_ignored=self.counters.duplicates_ignored,
                malformed_messages=self.counters.malformed_messages,
                reconnect_count=self.counters.reconnect_count,
                observations_written=self.counters.observations_written,
                snapshots_written=self.counters.snapshots_written,
                segments_updated=self.counters.segments_updated,
            )
        except Exception:
            logger.exception("AIS data flush succeeded but provider health state could not be updated")
        return {
            "dryRun": False,
            "observations": observations_written,
            "snapshots": int(snapshot_result.get("snapshots") or 0),
            "segments": int(snapshot_result.get("segments") or 0),
            "counters": self.counters.public_dict(),
        }

    async def run(self, *, duration_seconds: int | None = None) -> dict[str, Any]:
        self.settings.require_api_key()
        if not self.dry_run:
            self.storage.ensure_schema()
            self.storage.sync_targets(self.targets)
            self.storage.update_provider_state(
                status="starting",
                configured=True,
                connected=False,
                worker_id=self.worker_id,
                started_at=utc_now().isoformat(),
            )
        deadline = time.monotonic() + duration_seconds if duration_seconds else None
        backoff = self.settings.reconnect_initial_seconds
        while not self._stop_event.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                break
            try:
                await self._consume_connection(deadline)
                backoff = self.settings.reconnect_initial_seconds
            except AisProviderMessageError as exc:
                error = self._redact(str(exc))
                status = "authentication_error" if "key" in error.casefold() else "provider_error"
                self._record_failure(status, error)
                raise RuntimeError(f"AISStream provider rejected the subscription: {error}") from exc
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.counters.reconnect_count += 1
                error = self._redact(f"{type(exc).__name__}: {exc}")
                logger.warning("AIS connection failed; reconnecting in %.1fs: %s", backoff, error)
                self._record_failure("reconnecting", error)
                sleep_seconds = min(backoff, self.settings.reconnect_max_seconds) * random.uniform(0.85, 1.15)
                if deadline is not None:
                    sleep_seconds = min(sleep_seconds, max(0.0, deadline - time.monotonic()))
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_seconds)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, self.settings.reconnect_max_seconds)
        final_result = self.flush()
        if not self.dry_run:
            self.storage.update_provider_state(
                status="stopped",
                configured=True,
                connected=False,
                worker_id=self.worker_id,
                stopped_at=utc_now().isoformat(),
            )
        return final_result

    async def _consume_connection(self, deadline: float | None) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("Missing websockets package. Run pip install -r requirements.txt") from exc
        async with websockets.connect(
            self.settings.endpoint,
            open_timeout=self.settings.open_timeout_seconds,
            ping_interval=self.settings.ping_interval_seconds,
            ping_timeout=self.settings.ping_timeout_seconds,
            max_queue=4096,
        ) as websocket:
            await websocket.send(json.dumps(self.subscription_message(), separators=(",", ":")))
            connected_at = utc_now()
            logger.info("AISStream connection established for %d target areas", len(self.targets))
            if not self.dry_run:
                self.storage.update_provider_state(
                    status="connected",
                    configured=True,
                    connected=True,
                    worker_id=self.worker_id,
                    last_connected_at=connected_at.isoformat(),
                    last_error=None,
                )
            next_flush = time.monotonic() + self.settings.flush_interval_seconds
            try:
                while not self._stop_event.is_set():
                    if deadline is not None and time.monotonic() >= deadline:
                        return
                    timeout = max(0.1, next_flush - time.monotonic())
                    if deadline is not None:
                        timeout = min(timeout, max(0.1, deadline - time.monotonic()))
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        self.flush()
                        next_flush = time.monotonic() + self.settings.flush_interval_seconds
                        continue
                    self.process_message(message, received_at=utc_now())
                    if time.monotonic() >= next_flush:
                        self.flush()
                        next_flush = time.monotonic() + self.settings.flush_interval_seconds
            finally:
                if self.pending:
                    self.flush()
                if not self.dry_run:
                    try:
                        self.storage.update_provider_state(
                            status="disconnected",
                            configured=True,
                            connected=False,
                            worker_id=self.worker_id,
                            disconnected_at=utc_now().isoformat(),
                        )
                    except Exception:
                        logger.exception("Failed to mark AIS provider disconnected")

    def _record_failure(self, status: str, error: str) -> None:
        if self.dry_run:
            return
        try:
            self.storage.update_provider_state(
                status=status,
                configured=True,
                connected=False,
                worker_id=self.worker_id,
                last_error=error[:500],
                last_error_at=utc_now().isoformat(),
                reconnect_count=self.counters.reconnect_count,
            )
        except Exception:
            logger.exception("Failed to persist AIS provider failure state")

    def _redact(self, value: str) -> str:
        secret = self.settings.api_key
        return value.replace(secret, "[REDACTED]") if secret else value
