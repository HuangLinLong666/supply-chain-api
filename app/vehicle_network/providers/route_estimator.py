from __future__ import annotations

import math
from typing import Any


MODE_SPEED = {"road": 65.0, "rail": 55.0, "sea": 32.0, "air": 760.0}


def haversine_km(origin: dict[str, Any], destination: dict[str, Any]) -> float:
    """计算两点球面距离。"""
    lat1, lon1 = math.radians(float(origin["latitude"])), math.radians(float(origin["longitude"]))
    lat2, lon2 = math.radians(float(destination["latitude"])), math.radians(float(destination["longitude"]))
    delta_lat, delta_lon = lat2 - lat1, lon2 - lon1
    value = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(value))


def estimate_leg(origin: dict[str, Any], destination: dict[str, Any], mode: str) -> dict[str, Any]:
    """在真实时刻表不可用时生成低置信度估算腿。"""
    direct = haversine_km(origin, destination)
    detour = {"road": 1.22, "rail": 1.18, "sea": 1.12, "air": 1.04}.get(mode, 1.2)
    distance = direct * detour
    handling = {"road": 2, "rail": 12, "sea": 36, "air": 8}.get(mode, 6)
    return {
        "distance_km": round(distance, 2),
        "duration_h": round(distance / MODE_SPEED.get(mode, 50) + handling, 2),
        "geometry": [[origin["longitude"], origin["latitude"]], [destination["longitude"], destination["latitude"]]],
    }
