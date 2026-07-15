from __future__ import annotations

import time
from typing import Any

import httpx

from gdelt.config import GdeltSettings


class GdeltClient:
    def __init__(self, settings: GdeltSettings | None = None, client: httpx.Client | None = None):
        self.settings = settings or GdeltSettings()
        self.client = client or httpx.Client(timeout=self.settings.timeout_seconds)
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.settings.min_request_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def search(self, query: str) -> list[dict[str, Any]]:
        parameters = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "sort": "DateDesc",
            "timespan": self.settings.timespan,
            "maxrecords": min(max(self.settings.max_records, 1), 250),
        }
        for attempt in range(self.settings.max_retries):
            try:
                self._throttle()
                response = self.client.get(self.settings.base_url, params=parameters)
                self._last_request_at = time.monotonic()
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError("retryable GDELT response", request=response.request, response=response)
                response.raise_for_status()
                if "limit requests to one every 5 seconds" in response.text.casefold():
                    raise ValueError("GDELT rate limit response")
                payload = response.json()
                articles = payload.get("articles", []) if isinstance(payload, dict) else []
                return [article for article in articles if isinstance(article, dict)]
            except (httpx.HTTPError, ValueError):
                if attempt + 1 >= self.settings.max_retries:
                    raise
                time.sleep(max(2**attempt, self.settings.min_request_interval_seconds))
        return []
