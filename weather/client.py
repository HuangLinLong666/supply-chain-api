"""Resilient batched Open-Meteo HTTP client."""

from __future__ import annotations

import time
from typing import Any

import httpx

from weather.config import WeatherSettings

CURRENT = "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,rain,showers,snowfall,weather_code,cloud_cover,surface_pressure,wind_speed_10m,wind_direction_10m,wind_gusts_10m"
HOURLY = "temperature_2m,relative_humidity_2m,precipitation,rain,showers,snowfall,weather_code,visibility,wind_speed_10m,wind_gusts_10m"
MARINE = "wave_height,wave_direction,wave_period,wind_wave_height,wind_wave_direction,wind_wave_period,swell_wave_height,swell_wave_direction,swell_wave_period"


class OpenMeteoClient:
    def __init__(self, settings: WeatherSettings | None = None, transport: httpx.BaseTransport | None = None):
        self.settings = settings or WeatherSettings()
        self.http = httpx.Client(timeout=self.settings.timeout_seconds, transport=transport)

    def close(self) -> None: self.http.close()

    def _get(self, url: str, params: dict[str, Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                response = self.http.get(url, params=params)
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError("retryable Open-Meteo response", request=response.request, response=response)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt >= self.settings.max_retries: break
                time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(f"Open-Meteo request failed after retries: {last_error}") from last_error

    def weather_batch(self, ports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = self._get(self.settings.weather_url, {"latitude": ",".join(str(p["latitude"]) for p in ports), "longitude": ",".join(str(p["longitude"]) for p in ports), "current": CURRENT, "hourly": HOURLY, "forecast_days": 2, "forecast_hours": 24, "timezone": "auto", "wind_speed_unit": "kmh"})
        return result if isinstance(result, list) else [result]

    def marine_batch(self, ports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = self._get(self.settings.marine_url, {"latitude": ",".join(str(p["latitude"]) for p in ports), "longitude": ",".join(str(p["longitude"]) for p in ports), "current": MARINE, "hourly": MARINE, "forecast_hours": 24, "timezone": "auto", "cell_selection": "sea"})
        return result if isinstance(result, list) else [result]

    def geocode(self, name: str, country_code: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"name": name, "count": 10, "language": "en"}
        if country_code: params["countryCode"] = country_code
        return self._get(self.settings.geocoding_url, params).get("results", [])
