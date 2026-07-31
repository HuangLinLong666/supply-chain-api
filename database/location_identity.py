from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any, Iterable

from database.country_identity import resolve_country_code


LOCATION_ID_VERSION = "location-id-v2"
LOCATION_ID_CONFIRMATION = "APPLY_LOCATION_ID_V2"

CITY_COUNTRY_CODES = {
    "alashankou": "CN",
    "chengdu": "CN",
    "chongqing": "CN",
    "guangzhou": "CN",
    "khorgos": "CN",
    "ningbo": "CN",
    "shanghai": "CN",
    "shenzhen": "CN",
    "xi'an": "CN",
    "xian": "CN",
    "yiwu": "CN",
    "zhengzhou": "CN",
}

KIND_PREFIXES = {
    "port": "PORT",
    "airport": "AIR",
    "rail_terminal": "RAIL",
    "road_terminal": "ROAD",
    "factory": "FAC",
    "warehouse": "WH",
}

KIND_LABELS = (
    ("Port", "port"),
    ("Airport", "airport"),
    ("RailTerminal", "rail_terminal"),
    ("RoadTerminal", "road_terminal"),
    ("Factory", "factory"),
    ("Warehouse", "warehouse"),
)


def token(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").upper()
    return re.sub(r"[^A-Z0-9]+", "-", ascii_value).strip("-")


def compact_code(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def location_kind(labels: Iterable[str], properties: dict[str, Any]) -> str:
    label_set = set(labels)
    for label, kind in KIND_LABELS:
        if label in label_set:
            return kind
    value = str(properties.get("location_kind") or "").strip().casefold()
    if value in KIND_PREFIXES:
        return value
    raise ValueError(f"无法识别地点类型: labels={sorted(label_set)}")


def country_code(properties: dict[str, Any]) -> str:
    explicit = resolve_country_code(
        properties.get("country_code"),
        properties.get("iso2"),
        properties.get("country"),
        properties.get("country_name_en"),
        properties.get("country_name_zh"),
    )
    if explicit:
        return explicit
    for field in ("canonical_unlocode", "unlocode"):
        external_code = compact_code(properties.get(field))
        if len(external_code) == 5:
            return external_code[:2]
    city = str(properties.get("city") or "").strip().casefold()
    return CITY_COUNTRY_CODES.get(city, "XX")


def _strip_prefix(value: str, prefixes: Iterable[str]) -> str:
    for prefix in prefixes:
        if value == prefix:
            return ""
        if value.startswith(f"{prefix}-"):
            return value[len(prefix) + 1 :]
    return value


def _remove_tokens(value: str, removals: Iterable[str]) -> str:
    removal_set = {item for item in removals if item}
    return "-".join(item for item in value.split("-") if item and item not in removal_set)


def _fallback_suffix(properties: dict[str, Any], element_id: str) -> str:
    source = "|".join(
        str(properties.get(field) or "")
        for field in ("name_en", "name", "name_zh", "city", "location_id")
    )
    normalized = token(source)
    if normalized:
        return normalized
    return hashlib.sha256(element_id.encode("utf-8")).hexdigest()[:10].upper()


def canonical_location_id(
    labels: Iterable[str],
    properties: dict[str, Any],
    *,
    element_id: str,
) -> str:
    kind = location_kind(labels, properties)
    prefix = KIND_PREFIXES[kind]
    country = country_code(properties)
    current_id = token(properties.get("location_id"))

    if kind == "port":
        code = compact_code(
            properties.get("canonical_unlocode")
            or properties.get("unlocode")
            or properties.get("port_id")
        )
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}", code):
            candidate = compact_code(properties.get("location_id"))
            code = candidate if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}", candidate) else ""
        return f"{prefix}-{code or country + '-' + _fallback_suffix(properties, element_id)}"

    if kind == "airport":
        iata = compact_code(properties.get("iata") or properties.get("iata_code"))
        icao = compact_code(properties.get("icao"))
        code = iata if re.fullmatch(r"[A-Z0-9]{3}", iata) else icao
        if not re.fullmatch(r"[A-Z0-9]{3,4}", code):
            suffix = _strip_prefix(current_id, ("AIR", country))
            code = suffix if re.fullmatch(r"[A-Z0-9]{3,4}", suffix) else _fallback_suffix(properties, element_id)
        return f"{prefix}-{code}"

    if kind in {"rail_terminal", "road_terminal"}:
        source = token(
            properties.get("terminal_code")
            or properties.get("road_terminal_id")
            or properties.get("location_id")
            or properties.get("name_en")
            or properties.get("name")
        )
        source = _strip_prefix(source, (prefix, country))
        source = re.sub(
            r"-(RAILWAY-PORT|RAILWAY-STATION|RAILWAY-CONTAINER-CENTER|RAIL-TERMINAL|ROAD-TERMINAL)$",
            "",
            source,
        )
        return f"{prefix}-{country}-{source or _fallback_suffix(properties, element_id)}"

    if kind == "factory":
        source = token(
            properties.get("factory_id")
            or properties.get("location_id")
            or properties.get("name_en")
            or properties.get("name")
        )
        source = _strip_prefix(source, ("FAC", "FACTORY", "SEC"))
        source = _remove_tokens(source, (country,))
        if not source:
            source = _fallback_suffix(properties, element_id)
        if len(source.split("-")) == 1 and properties.get("city"):
            source = f"{source}-{token(properties['city'])}"
        return f"{prefix}-{country}-{source}"

    source = token(
        properties.get("warehouse_id")
        or properties.get("location_id")
        or properties.get("name_en")
        or properties.get("name")
    )
    source = _strip_prefix(source, ("WH", "WAREHOUSE"))
    source = _remove_tokens(source, (country,))
    country_name = token(properties.get("country"))
    if country_name and (source == country_name or source.startswith(f"{country_name}-")):
        source = source[len(country_name) :].strip("-")
    source = re.sub(r"-WAREHOUSE$", "", source)
    return f"{prefix}-{country}-{source or _fallback_suffix(properties, element_id)}"


def location_aliases(properties: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    for field in (
        "location_id",
        "canonical_location_id",
        "unlocode",
        "canonical_unlocode",
        "iata",
        "iata_code",
        "icao",
        "port_id",
        "terminal_code",
        "road_terminal_id",
        "factory_id",
        "warehouse_id",
        "code",
        "id",
    ):
        value = str(properties.get(field) or "").strip()
        if value and value not in aliases:
            aliases.append(value)
    for value in properties.get("location_aliases") or []:
        alias = str(value).strip()
        if alias and alias not in aliases:
            aliases.append(alias)
    return aliases


def build_location_id_plan(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    used: dict[str, str] = {}
    collisions: list[dict[str, str]] = []
    kind_counts: Counter[str] = Counter()
    for node in sorted(nodes, key=lambda item: str(item["element_id"])):
        element_id = str(node["element_id"])
        labels = [str(label) for label in node.get("labels") or []]
        properties = dict(node.get("properties") or {})
        old_id = str(properties.get("location_id") or "").strip()
        if not old_id:
            raise ValueError(f"地点 {element_id} 缺少 location_id，不能安全替换")
        kind = location_kind(labels, properties)
        desired_id = canonical_location_id(labels, properties, element_id=element_id)
        new_id = desired_id
        if new_id in used and used[new_id] != element_id:
            suffix = hashlib.sha256(element_id.encode("utf-8")).hexdigest()[:8].upper()
            new_id = f"{desired_id}-{suffix}"
            collisions.append(
                {
                    "desired_id": desired_id,
                    "assigned_id": new_id,
                    "existing_element_id": used[desired_id],
                    "element_id": element_id,
                }
            )
        used[new_id] = element_id
        kind_counts[kind] += 1
        aliases = location_aliases(properties)
        if old_id not in aliases:
            aliases.insert(0, old_id)
        rows.append(
            {
                "element_id": element_id,
                "kind": kind,
                "old_id": old_id,
                "new_id": new_id,
                "temporary_id": f"MIGRATING-{hashlib.sha256(element_id.encode('utf-8')).hexdigest()[:16].upper()}",
                "aliases": aliases,
                "country_code": country_code(properties),
                "changed": old_id != new_id,
            }
        )
    old_id_owners = {row["old_id"]: row["new_id"] for row in rows}
    alias_owners: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        for alias in row["aliases"]:
            alias_owners[str(alias)].add(str(row["new_id"]))
    aliases_by_new_id: dict[str, list[str]] = defaultdict(list)
    for alias, owners in alias_owners.items():
        owner = old_id_owners.get(alias)
        if owner is None and len(owners) == 1:
            owner = next(iter(owners))
        if owner is not None:
            aliases_by_new_id[owner].append(alias)
    for row in rows:
        row["reference_aliases"] = sorted(
            set(aliases_by_new_id[row["new_id"]]) | {row["old_id"], row["new_id"]}
        )
    return {
        "version": LOCATION_ID_VERSION,
        "location_count": len(rows),
        "changed_count": sum(1 for row in rows if row["changed"]),
        "unchanged_count": sum(1 for row in rows if not row["changed"]),
        "kind_counts": dict(sorted(kind_counts.items())),
        "collision_count": len(collisions),
        "collisions": collisions,
        "rows": rows,
    }
