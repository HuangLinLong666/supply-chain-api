from __future__ import annotations

from typing import Any


EUROPE = {"netherlands", "germany", "belgium", "france", "spain", "italy", "united kingdom", "poland"}
EAST_ASIA = {"china", "singapore", "japan", "south korea", "korea", "vietnam", "thailand", "malaysia", "indonesia", "philippines"}
SOUTH_ASIA = {"india", "pakistan", "bangladesh", "sri lanka"}
MIDDLE_EAST = {"united arab emirates", "uae", "saudi arabia", "oman", "qatar", "bahrain", "kuwait", "iran", "iraq", "israel", "jordan", "yemen"}
AMERICAS = {"united states", "usa", "canada", "mexico", "brazil", "chile", "peru", "panama"}
OCEANIA = {"australia", "new zealand"}
CAPE_ALIASES = ("cape town", "cape of good hope", "durban", "south africa", "好望角", "开普敦")
SEA_MODES = {"sea", "ocean", "maritime"}
AIR_MODES = {"air", "aviation"}


def node_text(segment: dict[str, Any]) -> str:
    fields = ("from_name", "from_city", "from_country", "to_name", "to_city", "to_country")
    return " ".join(str(segment.get(field) or "") for field in fields).casefold()


def endpoint_regions(segment: dict[str, Any]) -> tuple[str, str]:
    return str(segment.get("from_country") or "").casefold(), str(segment.get("to_country") or "").casefold()


def crosses(left: str, right: str, first: set[str], second: set[str]) -> bool:
    return (left in first and right in second) or (right in first and left in second)


def inferred_exposure(zone_id: str, segment: dict[str, Any]) -> bool:
    mode = str(segment.get("mode") or "").casefold()
    text = node_text(segment)
    left, right = endpoint_regions(segment)
    if zone_id == "red-sea":
        return mode in SEA_MODES and not any(alias in text for alias in CAPE_ALIASES) and crosses(left, right, EAST_ASIA | SOUTH_ASIA, EUROPE)
    if zone_id == "malacca-strait":
        return mode in SEA_MODES and (
            "singapore" in {left, right}
            or crosses(left, right, EAST_ASIA, EUROPE | MIDDLE_EAST | SOUTH_ASIA)
        )
    if zone_id == "indian-ocean":
        return mode in SEA_MODES and crosses(left, right, EAST_ASIA | SOUTH_ASIA, EUROPE | MIDDLE_EAST)
    if zone_id == "pacific-ocean":
        return mode in SEA_MODES and crosses(left, right, EAST_ASIA | OCEANIA, AMERICAS)
    if zone_id == "middle-east":
        return mode in SEA_MODES | AIR_MODES and (
            left in MIDDLE_EAST or right in MIDDLE_EAST or crosses(left, right, EAST_ASIA | SOUTH_ASIA, EUROPE)
        )
    if zone_id == "hormuz-strait":
        return mode in SEA_MODES and crosses(left, right, MIDDLE_EAST, EAST_ASIA | SOUTH_ASIA | EUROPE | AMERICAS)
    if zone_id == "south-china-sea":
        return mode in SEA_MODES and (left in EAST_ASIA or right in EAST_ASIA) and "singapore" in {left, right}
    return False


def exposed_zone_ids(segment: dict[str, Any], zones: list[dict[str, Any]]) -> list[str]:
    exposed: list[str] = []
    text = node_text(segment)
    for zone in zones:
        alias_match = any(alias.casefold() in text for alias in zone.get("aliases", []))
        if alias_match or inferred_exposure(zone["id"], segment):
            exposed.append(zone["id"])
    return exposed
