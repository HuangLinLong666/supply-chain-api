from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from app.vehicle_network.models import StrategyConfig


ROOT = Path(__file__).resolve().parents[2]
STRATEGY_PATH = ROOT / "config" / "vehicle_strategy.yaml"
RATES_PATH = ROOT / "config" / "vehicle_rates.yaml"


def load_yaml(path: Path) -> dict[str, Any]:
    """加载 YAML；文件不存在时给出明确错误。"""
    if not path.exists():
        raise RuntimeError(f"配置文件不存在: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_strategy() -> StrategyConfig:
    return StrategyConfig.model_validate(load_yaml(STRATEGY_PATH))


def save_strategy(strategy: StrategyConfig) -> None:
    """保存策略配置，供单机版直接使用。生产环境建议改为配置中心。"""
    STRATEGY_PATH.write_text(yaml.safe_dump(strategy.model_dump(), allow_unicode=True, sort_keys=False), encoding="utf-8")


def load_rates() -> dict[str, Any]:
    return load_yaml(RATES_PATH)


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def provider_enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(f"ENABLE_PROVIDER_{name.upper()}", str(default))
    return value.casefold() in {"1", "true", "yes", "on"}
