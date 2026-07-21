from __future__ import annotations

import os
from typing import Any

from app.vehicle_network.providers.base import HttpProvider


class OpenMeteoRiskProvider(HttpProvider):
    """按坐标获取当前天气，返回供风险模型消费的标准指标。"""

    async def collect(self, latitude: float, longitude: float, trace_id: str) -> dict[str, Any]:
        url = os.getenv("OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1/forecast")
        payload = await self.get_json(url, {
            "latitude": latitude,
            "longitude": longitude,
            "current": "wind_speed_10m,precipitation,weather_code",
        }, trace_id)
        return payload.get("current", {})
