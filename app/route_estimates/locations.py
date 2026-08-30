from __future__ import annotations

import re
from typing import Any, Callable


QueryFunction = Callable[[str, dict[str, Any] | None], list[dict[str, Any]]]


class LocationResolutionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


PORT_QUERY = """
MATCH (location:TransportLocation:Port:RoutePlanningPort)
WHERE location.location_id = $location_id
RETURN location.location_id AS location_id,
       location.location_id_version AS location_id_version,
       location.canonical_unlocode AS canonical_unlocode
LIMIT 2
"""


def resolve_port_unlocode(location_id: str, query: QueryFunction) -> str:
    rows = query(PORT_QUERY, {"location_id": location_id})
    if not rows:
        raise LocationResolutionError("unknown_port", f"未找到受审计港口地点: {location_id}")
    if len(rows) != 1:
        raise LocationResolutionError("ambiguous_port", f"港口地点不能唯一解析: {location_id}")
    row = rows[0]
    if row.get("location_id") != location_id or row.get("location_id_version") != "location-id-v2":
        raise LocationResolutionError("invalid_location_identity", f"地点不是有效的 location-id-v2 港口: {location_id}")
    unlocode = str(row.get("canonical_unlocode") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}", unlocode):
        raise LocationResolutionError("missing_canonical_unlocode", f"港口缺少规范 UN/LOCODE: {location_id}")
    return unlocode
