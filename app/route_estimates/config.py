from __future__ import annotations

import os
from dataclasses import dataclass, field


FREIGHTOS_PUBLIC_ENDPOINT = "https://ship.freightos.com/api/shippingCalculator"
FREIGHTOS_ATTRIBUTION_URL = "https://ship.freightos.com"


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name, str(default)).strip().casefold()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} 必须是 true 或 false")
    return value == "true"


@dataclass(frozen=True)
class RouteEstimateSettings:
    timeout_seconds: float = field(default_factory=lambda: float(os.getenv("FREIGHTOS_TIMEOUT_SECONDS", "15")))
    max_retries: int = field(default_factory=lambda: int(os.getenv("FREIGHTOS_MAX_RETRIES", "2")))
    cache_ttl_seconds: int = field(default_factory=lambda: int(os.getenv("FREIGHTOS_CACHE_TTL_SECONDS", "900")))
    circuit_failure_threshold: int = field(default_factory=lambda: int(os.getenv("FREIGHTOS_CIRCUIT_FAILURE_THRESHOLD", "3")))
    circuit_open_seconds: int = field(default_factory=lambda: int(os.getenv("FREIGHTOS_CIRCUIT_OPEN_SECONDS", "300")))
    hourly_request_limit: int = field(
        default_factory=lambda: int(os.getenv("FREIGHTOS_HOURLY_REQUEST_LIMIT", "100"))
    )
    service_token: str = field(default_factory=lambda: os.getenv("ROUTE_ESTIMATE_SERVICE_TOKEN", ""))
    app_environment: str = field(default_factory=lambda: os.getenv("APP_ENV", "development").strip().casefold())
    allow_local_unauthenticated: bool = field(
        default_factory=lambda: env_bool("ROUTE_ESTIMATE_ALLOW_LOCAL_UNAUTHENTICATED", False)
    )

    def __post_init__(self) -> None:
        ranges = {
            "FREIGHTOS_TIMEOUT_SECONDS": (self.timeout_seconds, 1, 60),
            "FREIGHTOS_MAX_RETRIES": (self.max_retries, 0, 5),
            "FREIGHTOS_CACHE_TTL_SECONDS": (self.cache_ttl_seconds, 1, 3600),
            "FREIGHTOS_HOURLY_REQUEST_LIMIT": (self.hourly_request_limit, 1, 100),
            "FREIGHTOS_CIRCUIT_FAILURE_THRESHOLD": (self.circuit_failure_threshold, 1, 20),
            "FREIGHTOS_CIRCUIT_OPEN_SECONDS": (self.circuit_open_seconds, 1, 3600),
        }
        for name, (value, minimum, maximum) in ranges.items():
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
