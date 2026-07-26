from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class NormalizedAisObservation:
    mmsi: str
    message_type: str
    observed_at: datetime
    received_at: datetime
    dedupe_key: str
    imo: str | None = None
    vessel_name: str | None = None
    vessel_type: int | None = None
    call_sign: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    speed_knots: float | None = None
    course_degrees: float | None = None
    heading_degrees: int | None = None
    destination: str | None = None
    draught_m: float | None = None
    navigational_status_code: int | None = None
    navigational_status: str | None = None
    position_observed_at: datetime | None = None
    static_observed_at: datetime | None = None
    target_ids: tuple[str, ...] = ()
    message_types: tuple[str, ...] = ()

    @property
    def has_position(self) -> bool:
        return self.latitude is not None and self.longitude is not None and self.position_observed_at is not None

    @property
    def has_static_data(self) -> bool:
        return self.static_observed_at is not None or any(
            value is not None
            for value in (self.imo, self.vessel_name, self.vessel_type, self.call_sign, self.destination, self.draught_m)
        )

    def with_targets(self, target_ids: tuple[str, ...]) -> "NormalizedAisObservation":
        return replace(self, target_ids=tuple(sorted(set((*self.target_ids, *target_ids)))))

    def storage_row(self) -> dict[str, Any]:
        return {
            "mmsi": self.mmsi,
            "message_type": self.message_type,
            "message_types": list(self.message_types or (self.message_type,)),
            "observed_at": self.observed_at.isoformat(),
            "received_at": self.received_at.isoformat(),
            "position_observed_at": self.position_observed_at.isoformat() if self.position_observed_at else None,
            "static_observed_at": self.static_observed_at.isoformat() if self.static_observed_at else None,
            "dedupe_key": self.dedupe_key,
            "imo": self.imo,
            "vessel_name": self.vessel_name,
            "vessel_type": self.vessel_type,
            "call_sign": self.call_sign,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "speed_knots": self.speed_knots,
            "course_degrees": self.course_degrees,
            "heading_degrees": self.heading_degrees,
            "destination": self.destination,
            "draught_m": self.draught_m,
            "navigational_status_code": self.navigational_status_code,
            "navigational_status": self.navigational_status,
            "target_ids": list(self.target_ids),
            "has_position": self.has_position,
            "has_static_data": self.has_static_data,
        }


def merge_observations(
    current: NormalizedAisObservation | None,
    incoming: NormalizedAisObservation,
) -> NormalizedAisObservation:
    if current is None:
        return incoming
    position_source = incoming if incoming.position_observed_at and (
        current.position_observed_at is None or incoming.position_observed_at >= current.position_observed_at
    ) else current
    newest = incoming if incoming.observed_at >= current.observed_at else current

    def latest_value(field_name: str) -> Any:
        incoming_value = getattr(incoming, field_name)
        return incoming_value if incoming_value is not None else getattr(current, field_name)

    return NormalizedAisObservation(
        mmsi=current.mmsi,
        message_type=newest.message_type,
        observed_at=max(current.observed_at, incoming.observed_at),
        received_at=max(current.received_at, incoming.received_at),
        dedupe_key=newest.dedupe_key,
        imo=latest_value("imo"),
        vessel_name=latest_value("vessel_name"),
        vessel_type=latest_value("vessel_type"),
        call_sign=latest_value("call_sign"),
        latitude=position_source.latitude,
        longitude=position_source.longitude,
        speed_knots=position_source.speed_knots,
        course_degrees=position_source.course_degrees,
        heading_degrees=position_source.heading_degrees,
        destination=latest_value("destination"),
        draught_m=latest_value("draught_m"),
        navigational_status_code=position_source.navigational_status_code,
        navigational_status=position_source.navigational_status,
        position_observed_at=position_source.position_observed_at,
        static_observed_at=max(
            (value for value in (current.static_observed_at, incoming.static_observed_at) if value is not None),
            default=None,
        ),
        target_ids=tuple(sorted(set((*current.target_ids, *incoming.target_ids)))),
        message_types=tuple(sorted(set((*(current.message_types or (current.message_type,)), *(incoming.message_types or (incoming.message_type,)))))),
    )
