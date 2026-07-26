from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_ENDPOINT = "wss://stream.aisstream.io/v0/stream"
DEFAULT_TARGETS_PATH = Path(__file__).resolve().parents[1] / "config" / "ais_observation_targets.json"


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
    return value


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True)
class BoundingBox:
    south: float
    west: float
    north: float
    east: float

    def __post_init__(self) -> None:
        if not (-90 <= self.south < self.north <= 90):
            raise ValueError("AIS latitude bounds are invalid")
        if not (-180 <= self.west < self.east <= 180):
            raise ValueError("AIS longitude bounds are invalid")

    @classmethod
    def from_json(cls, value: Any) -> "BoundingBox":
        if not isinstance(value, list) or len(value) != 2 or any(not isinstance(corner, list) or len(corner) != 2 for corner in value):
            raise ValueError("AIS bounding box must be [[[south, west], [north, east]]] without the outer list")
        first, second = value
        latitudes = sorted((float(first[0]), float(second[0])))
        longitudes = sorted((float(first[1]), float(second[1])))
        return cls(south=latitudes[0], west=longitudes[0], north=latitudes[1], east=longitudes[1])

    def contains(self, latitude: float, longitude: float) -> bool:
        return self.south <= latitude <= self.north and self.west <= longitude <= self.east

    def as_subscription_box(self) -> list[list[float]]:
        return [[self.south, self.west], [self.north, self.east]]


@dataclass(frozen=True)
class AisTarget:
    target_id: str
    name: str
    target_type: str
    aggregation_bbox: BoundingBox
    subscription_bbox: BoundingBox
    port_ids: tuple[str, ...] = ()
    zone_id: str | None = None
    congestion_reference_vessel_count: int = 50
    slow_speed_knots: float = 1.0

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "AisTarget":
        target_id = str(value.get("id") or "").strip()
        name = str(value.get("name") or "").strip()
        target_type = str(value.get("type") or "").strip().casefold()
        if not target_id or not name:
            raise ValueError("Each AIS target requires id and name")
        if target_type not in {"port", "corridor"}:
            raise ValueError(f"AIS target {target_id} has unsupported type {target_type!r}")
        target = cls(
            target_id=target_id,
            name=name,
            target_type=target_type,
            aggregation_bbox=BoundingBox.from_json(value.get("aggregation_bbox")),
            subscription_bbox=BoundingBox.from_json(value.get("subscription_bbox")),
            port_ids=tuple(str(item).strip() for item in value.get("port_ids") or [] if str(item).strip()),
            zone_id=str(value.get("zone_id") or "").strip() or None,
            congestion_reference_vessel_count=max(1, int(value.get("congestion_reference_vessel_count") or 50)),
            slow_speed_knots=max(0.0, float(value.get("slow_speed_knots") or 1.0)),
        )
        for latitude, longitude in (
            (target.aggregation_bbox.south, target.aggregation_bbox.west),
            (target.aggregation_bbox.north, target.aggregation_bbox.east),
        ):
            if not target.subscription_bbox.contains(latitude, longitude):
                raise ValueError(f"AIS target {target_id} aggregation_bbox must be inside subscription_bbox")
        if target.target_type == "port" and not target.port_ids:
            raise ValueError(f"AIS port target {target_id} requires port_ids")
        if target.target_type == "corridor" and not target.zone_id:
            raise ValueError(f"AIS corridor target {target_id} requires zone_id")
        return target

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.target_id,
            "name": self.name,
            "type": self.target_type,
            "portIds": list(self.port_ids),
            "zoneId": self.zone_id,
            "aggregationBoundingBox": self.aggregation_bbox.as_subscription_box(),
            "subscriptionBoundingBox": self.subscription_bbox.as_subscription_box(),
        }


@dataclass(frozen=True)
class AisSettings:
    endpoint: str = DEFAULT_ENDPOINT
    api_key: str | None = field(default=None, repr=False)
    target_config_path: Path = DEFAULT_TARGETS_PATH
    flush_interval_seconds: int = 60
    window_minutes: int = 60
    snapshot_ttl_minutes: int = 90
    provider_stale_seconds: int = 300
    reconnect_initial_seconds: float = 2.0
    reconnect_max_seconds: float = 60.0
    open_timeout_seconds: int = 20
    ping_interval_seconds: int = 20
    ping_timeout_seconds: int = 20
    dedupe_ttl_seconds: int = 600
    dedupe_max_entries: int = 100_000

    @classmethod
    def from_environment(cls) -> "AisSettings":
        return cls(
            endpoint=os.getenv("AISSTREAM_ENDPOINT", DEFAULT_ENDPOINT).strip(),
            api_key=os.getenv("AISSTREAM_API_KEY") or None,
            target_config_path=Path(os.getenv("AIS_TARGETS_CONFIG", str(DEFAULT_TARGETS_PATH))).expanduser(),
            flush_interval_seconds=_env_int("AIS_FLUSH_INTERVAL_SECONDS", 60),
            window_minutes=_env_int("AIS_AGGREGATION_WINDOW_MINUTES", 60),
            snapshot_ttl_minutes=_env_int("AIS_SNAPSHOT_TTL_MINUTES", 90),
            provider_stale_seconds=_env_int("AIS_PROVIDER_STALE_SECONDS", 300),
            reconnect_initial_seconds=_env_float("AIS_RECONNECT_INITIAL_SECONDS", 2.0),
            reconnect_max_seconds=_env_float("AIS_RECONNECT_MAX_SECONDS", 60.0),
            open_timeout_seconds=_env_int("AIS_OPEN_TIMEOUT_SECONDS", 20),
            ping_interval_seconds=_env_int("AIS_PING_INTERVAL_SECONDS", 20),
            ping_timeout_seconds=_env_int("AIS_PING_TIMEOUT_SECONDS", 20),
            dedupe_ttl_seconds=_env_int("AIS_DEDUPE_TTL_SECONDS", 600),
            dedupe_max_entries=_env_int("AIS_DEDUPE_MAX_ENTRIES", 100_000),
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def require_api_key(self) -> str:
        if not self.api_key:
            raise RuntimeError("Missing AISSTREAM_API_KEY. Configure it only in the backend worker environment.")
        return self.api_key

    def public_dict(self) -> dict[str, Any]:
        return {
            "provider": "AISStream.io",
            "configured": self.configured,
            "endpoint": self.endpoint,
            "targetConfigPath": str(self.target_config_path),
            "flushIntervalSeconds": self.flush_interval_seconds,
            "windowMinutes": self.window_minutes,
            "snapshotTtlMinutes": self.snapshot_ttl_minutes,
        }


def load_targets(path: Path | str | None = None) -> list[AisTarget]:
    config_path = Path(path or DEFAULT_TARGETS_PATH)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    targets = [AisTarget.from_json(item) for item in payload.get("targets") or []]
    if not targets:
        raise RuntimeError(f"No AIS targets found in {config_path}")
    identifiers = [target.target_id for target in targets]
    if len(identifiers) != len(set(identifiers)):
        raise RuntimeError(f"Duplicate AIS target id in {config_path}")
    return targets
