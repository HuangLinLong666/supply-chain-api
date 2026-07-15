from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GdeltSettings:
    base_url: str = os.getenv("GDELT_DOC_API_URL", "https://api.gdeltproject.org/api/v2/doc/doc")
    timespan: str = os.getenv("GDELT_TIMESPAN", "24h")
    max_records: int = int(os.getenv("GDELT_MAX_RECORDS", "100"))
    timeout_seconds: float = float(os.getenv("GDELT_REQUEST_TIMEOUT_SECONDS", "30"))
    max_retries: int = int(os.getenv("GDELT_MAX_RETRIES", "3"))
    min_request_interval_seconds: float = float(os.getenv("GDELT_MIN_REQUEST_INTERVAL_SECONDS", "6"))
    risk_ttl_hours: int = int(os.getenv("GDELT_RISK_TTL_HOURS", "3"))
    admin_token: str = os.getenv("GDELT_ADMIN_TOKEN", "")


def load_zone_config() -> dict[str, Any]:
    path = Path(os.getenv("GDELT_RISK_ZONES_FILE", "config/gdelt_risk_zones.json"))
    return json.loads(path.read_text(encoding="utf-8"))
