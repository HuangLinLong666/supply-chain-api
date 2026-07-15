"""Environment-backed weather module configuration."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WeatherSettings:
    weather_url: str = os.getenv("OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1/forecast")
    marine_url: str = os.getenv("OPEN_METEO_MARINE_BASE_URL", "https://marine-api.open-meteo.com/v1/marine")
    geocoding_url: str = os.getenv("OPEN_METEO_GEOCODING_URL", "https://geocoding-api.open-meteo.com/v1/search")
    update_interval_minutes: int = int(os.getenv("WEATHER_UPDATE_INTERVAL_MINUTES", "60"))
    timeout_seconds: float = float(os.getenv("WEATHER_REQUEST_TIMEOUT_SECONDS", "20"))
    max_retries: int = int(os.getenv("WEATHER_MAX_RETRIES", "3"))
    batch_size: int = int(os.getenv("WEATHER_BATCH_SIZE", "25"))
    cache_ttl_minutes: int = int(os.getenv("WEATHER_CACHE_TTL_MINUTES", "45"))
    retention_days: int = int(os.getenv("WEATHER_SNAPSHOT_RETENTION_DAYS", "30"))
    admin_token: str = os.getenv("WEATHER_ADMIN_TOKEN", "")
    scheduler_enabled: bool = os.getenv("WEATHER_SCHEDULER_ENABLED", "false").lower() == "true"


def load_rules() -> dict[str, Any]:
    path = Path(__file__).resolve().parent.parent / "config" / "weather_risk_rules.json"
    return json.loads(path.read_text())
