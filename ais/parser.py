from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

from ais.models import NormalizedAisObservation, utc_now


POSITION_MESSAGE_TYPES = {
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "LongRangeAisBroadcastMessage",
}
STATIC_MESSAGE_TYPES = {"ShipStaticData", "StaticDataReport", "ExtendedClassBPositionReport"}
SUPPORTED_MESSAGE_TYPES = POSITION_MESSAGE_TYPES | STATIC_MESSAGE_TYPES

NAVIGATIONAL_STATUS_LABELS = {
    0: "under_way_using_engine",
    1: "at_anchor",
    2: "not_under_command",
    3: "restricted_manoeuvrability",
    4: "constrained_by_draught",
    5: "moored",
    6: "aground",
    7: "engaged_in_fishing",
    8: "under_way_sailing",
    9: "reserved_hsc",
    10: "reserved_wig",
    11: "reserved",
    12: "reserved",
    13: "reserved",
    14: "ais_sart",
    15: "undefined",
}


class AisMessageError(ValueError):
    pass


class AisProviderMessageError(AisMessageError):
    pass


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _case_get(mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    lowered = {str(key).casefold(): value for key, value in mapping.items()}
    for name in names:
        if name.casefold() in lowered:
            return lowered[name.casefold()]
    return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\x00", "").replace("@", " ")
    text = " ".join(text.split()).strip()
    return text or None


def _integer(value: Any, *, minimum: int | None = None, maximum: int | None = None) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    if minimum is not None and result < minimum:
        return None
    if maximum is not None and result > maximum:
        return None
    return result


def _number(value: Any, *, minimum: float | None = None, maximum: float | None = None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    if minimum is not None and result < minimum:
        return None
    if maximum is not None and result > maximum:
        return None
    return result


def parse_ais_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value is None:
        parsed = fallback
    else:
        text = str(value).strip()
        candidates = [text, text.removesuffix(" UTC").strip()]
        parsed = None
        for candidate in candidates:
            try:
                parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
                break
            except ValueError:
                pass
            for pattern in ("%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z"):
                try:
                    parsed = datetime.strptime(candidate, pattern)
                    break
                except ValueError:
                    pass
            if parsed is not None:
                break
        if parsed is None:
            parsed = fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _mmsi(body: dict[str, Any], metadata: dict[str, Any]) -> str | None:
    value = _case_get(metadata, "MMSI") or _case_get(body, "UserID")
    number = _integer(value, minimum=100_000_000, maximum=999_999_999)
    return str(number) if number is not None else None


def parse_ais_message(
    payload: str | bytes | dict[str, Any],
    *,
    received_at: datetime | None = None,
) -> NormalizedAisObservation | None:
    received = received_at or utc_now()
    if isinstance(payload, (str, bytes)):
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AisMessageError("AIS message is not valid JSON") from exc
    elif isinstance(payload, dict):
        data = payload
    else:
        raise AisMessageError("AIS message must be JSON text or an object")
    if not isinstance(data, dict):
        raise AisMessageError("AIS message root must be an object")
    provider_error = _clean_text(_case_get(data, "error"))
    if provider_error:
        raise AisProviderMessageError(provider_error)
    message_type = _clean_text(_case_get(data, "MessageType"))
    if not message_type or message_type not in SUPPORTED_MESSAGE_TYPES:
        return None
    messages = _mapping(_case_get(data, "Message"))
    body = _mapping(_case_get(messages, message_type))
    if not body:
        raise AisMessageError(f"AIS {message_type} body is missing")
    metadata = _mapping(_case_get(data, "MetaData", "Metadata"))
    mmsi = _mmsi(body, metadata)
    if not mmsi:
        raise AisMessageError("AIS message has no valid nine-digit MMSI")
    observed_at = parse_ais_datetime(_case_get(metadata, "time_utc", "timeUtc", "timestamp"), received)
    is_position = message_type in POSITION_MESSAGE_TYPES
    is_static = message_type in STATIC_MESSAGE_TYPES

    latitude = _number(_case_get(body, "Latitude"), minimum=-90, maximum=90) if is_position else None
    longitude = _number(_case_get(body, "Longitude"), minimum=-180, maximum=180) if is_position else None
    if is_position and (latitude is None or longitude is None):
        latitude = _number(_case_get(metadata, "latitude", "Latitude"), minimum=-90, maximum=90)
        longitude = _number(_case_get(metadata, "longitude", "Longitude"), minimum=-180, maximum=180)
    position_observed_at = observed_at if latitude is not None and longitude is not None else None

    report_a = _mapping(_case_get(body, "ReportA"))
    report_b = _mapping(_case_get(body, "ReportB"))
    vessel_name = _clean_text(
        _case_get(body, "Name") or _case_get(report_a, "Name") or _case_get(metadata, "ShipName")
    )
    vessel_type = _integer(
        _case_get(body, "Type") or _case_get(report_b, "ShipType"),
        minimum=1,
        maximum=99,
    )
    imo_number = _integer(_case_get(body, "ImoNumber"), minimum=1)
    draught = _number(_case_get(body, "MaximumStaticDraught"), minimum=0.1, maximum=30.0)
    navigation_code = _integer(_case_get(body, "NavigationalStatus"), minimum=0, maximum=15)
    speed = _number(_case_get(body, "Sog"), minimum=0, maximum=102.2) if is_position else None
    course = _number(_case_get(body, "Cog"), minimum=0, maximum=359.9) if is_position else None
    heading = _integer(_case_get(body, "TrueHeading"), minimum=0, maximum=359) if is_position else None
    canonical_payload = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    dedupe_key = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()

    return NormalizedAisObservation(
        mmsi=mmsi,
        message_type=message_type,
        observed_at=observed_at,
        received_at=received.astimezone(timezone.utc),
        dedupe_key=dedupe_key,
        imo=str(imo_number) if imo_number is not None else None,
        vessel_name=vessel_name,
        vessel_type=vessel_type,
        call_sign=_clean_text(_case_get(body, "CallSign") or _case_get(report_b, "CallSign")),
        latitude=latitude,
        longitude=longitude,
        speed_knots=speed,
        course_degrees=course,
        heading_degrees=heading,
        destination=_clean_text(_case_get(body, "Destination")),
        draught_m=draught,
        navigational_status_code=navigation_code,
        navigational_status=NAVIGATIONAL_STATUS_LABELS.get(navigation_code) if navigation_code is not None else None,
        position_observed_at=position_observed_at,
        static_observed_at=observed_at if is_static else None,
        message_types=(message_type,),
    )
