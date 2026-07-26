from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def load_recommendation_settings() -> dict[str, Any]:
    with (PROJECT_ROOT / "config" / "recommendation_scoring.yaml").open(encoding="utf-8") as handle:
        scoring = yaml.safe_load(handle) or {}
    with (PROJECT_ROOT / "config" / "vehicle_rates.yaml").open(encoding="utf-8") as handle:
        rates = yaml.safe_load(handle) or {}
    scoring["cost_model"] = {**rates, **(scoring.get("cost_model") or {})}
    return scoring
