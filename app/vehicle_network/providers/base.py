from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.vehicle_network.models import LocationIngestRequest, LocationRecord


logger = logging.getLogger(__name__)


class LocationProvider(ABC):
    name: str

    @abstractmethod
    async def collect(self, request: LocationIngestRequest, trace_id: str) -> list[LocationRecord]:
        """采集并标准化地点。"""


class HttpProvider:
    """提供统一的 429、5xx、超时重试逻辑。"""

    def __init__(self, timeout: float = 30, max_retries: int = 4):
        self.client = httpx.AsyncClient(timeout=timeout)
        self.max_retries = max_retries

    async def get_json(self, url: str, parameters: dict[str, Any] | None, trace_id: str) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = await self.client.get(url, params=parameters)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", 0) or 0)
                    await asyncio.sleep(retry_after or 2**attempt + random.random())
                    continue
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError("服务端临时错误", request=response.request, response=response)
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError as exc:
                    logger.error("解析失败 trace_id=%s url=%s raw=%r", trace_id, url, response.text[:500])
                    raise exc
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    await asyncio.sleep(2**attempt + random.random())
        raise RuntimeError(f"Provider 请求失败 trace_id={trace_id}: {last_error}")
