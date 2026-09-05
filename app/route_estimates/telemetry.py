"""Minimal, redacted stage telemetry for route-estimate diagnostics."""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, Iterator


logger = logging.getLogger(__name__)

STAGE_OUTCOMES: dict[str, frozenset[str]] = {
    "request_received": frozenset({"completed"}),
    "authentication": frozenset({"accepted", "rejected", "configuration_unavailable", "not_observed"}),
    "location_resolution": frozenset(
        {
            "completed",
            "unknown_port",
            "ambiguous_port",
            "invalid_identity",
            "missing_unlocode",
            "unavailable",
            "not_reached",
        }
    ),
    "provider_cache": frozenset({"hit", "miss", "expired", "not_reached"}),
    "provider_call": frozenset(
        {
            "called",
            "not_called",
            "circuit_open",
            "rate_limited",
            "not_reached",
            "available",
            "partial",
            "unsupported",
            "timeout",
            "network",
            "http_4xx",
            "http_5xx",
            "invalid_response",
            "unavailable",
            "not_observed",
        }
    ),
    "response_mapping": frozenset({"completed", "client_error", "service_unavailable", "not_reached"}),
    "completed": frozenset({"completed", "client_error", "service_unavailable"}),
}


@dataclass(frozen=True)
class TelemetryContext:
    started_at: float
    monotonic: Callable[[], float]


_context: ContextVar[TelemetryContext | None] = ContextVar("route_estimate_telemetry", default=None)


def elapsed_bucket(elapsed_ms: float | None) -> str:
    if elapsed_ms is None or elapsed_ms < 0:
        return "unknown"
    if elapsed_ms < 100:
        return "lt_100ms"
    if elapsed_ms < 500:
        return "100_499ms"
    if elapsed_ms < 2_000:
        return "500_1999ms"
    if elapsed_ms < 5_000:
        return "2_4s"
    if elapsed_ms < 10_000:
        return "5_9s"
    if elapsed_ms < 30_000:
        return "10_29s"
    return "gte_30s"


@contextmanager
def route_estimate_telemetry(
    monotonic: Callable[[], float] = time.monotonic,
) -> Iterator[None]:
    token = _context.set(TelemetryContext(started_at=monotonic(), monotonic=monotonic))
    try:
        yield
    finally:
        _context.reset(token)


def record_route_estimate_stage(stage: str, outcome: str) -> bool:
    context = _context.get()
    if context is None:
        return False
    allowed_outcomes = STAGE_OUTCOMES.get(stage)
    if allowed_outcomes is None or outcome not in allowed_outcomes:
        raise ValueError("Unsupported route-estimate telemetry stage or outcome")
    elapsed_ms = max(0.0, (context.monotonic() - context.started_at) * 1_000)
    payload = {
        "event": "route_estimate_stage",
        "stage": stage,
        "outcome": outcome,
        "elapsedBucket": elapsed_bucket(elapsed_ms),
    }
    logger.info(json.dumps(payload, separators=(",", ":"), sort_keys=False))
    return True
