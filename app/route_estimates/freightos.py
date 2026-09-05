from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

import httpx

from app.route_estimates.config import FREIGHTOS_PUBLIC_ENDPOINT, RouteEstimateSettings
from app.route_estimates.models import RouteEstimateCargo, ShipmentMethod


logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
MAX_PROVIDER_RESPONSE_BYTES = 1_048_576


ProviderOutcome = Literal[
    "timeout",
    "network",
    "http_4xx",
    "http_5xx",
    "invalid_response",
    "unavailable",
]


class FreightosProviderError(RuntimeError):
    def __init__(self, message: str, outcome: ProviderOutcome = "unavailable"):
        super().__init__(message)
        self.outcome = outcome


@dataclass(frozen=True)
class FreightosEstimate:
    currency: str | None
    cost_min: float | None
    cost_max: float | None
    transit_min_days: float | None
    transit_max_days: float | None
    response_sha256: str


LOAD_TYPES = {
    "20ft": "container20",
    "40ft": "container40",
    "40hc": "container40HC",
}


def build_freightos_parameters(
    origin_unlocode: str,
    destination_unlocode: str,
    cargo: RouteEstimateCargo,
) -> dict[str, str | int | float]:
    parameters: dict[str, str | int | float] = {
        "estimate": "true",
        "format": "json",
        "currency": "USD",
        "origin": origin_unlocode,
        "destination": destination_unlocode,
        "quantity": cargo.quantity,
    }
    if cargo.shipment_method == ShipmentMethod.CONTAINER:
        parameters["loadtype"] = LOAD_TYPES[cargo.container_type.value]
        parameters["mode"] = "FCL"
    elif cargo.shipment_method == ShipmentMethod.LCL:
        parameters["loadtype"] = "boxes"
        parameters["mode"] = "LCL"
    else:
        raise ValueError("Freightos 公共市场不支持 RoRo")
    if cargo.gross_weight_kg is not None:
        parameters["weight"] = round(cargo.gross_weight_kg / cargo.quantity, 6)
    if cargo.volume_m3 is not None:
        parameters["volume"] = f"{cargo.volume_m3 / cargo.quantity:.6f}cbm"
    if cargo.length_cm is not None:
        parameters["length"] = round(cargo.length_cm, 6)
        parameters["width"] = round(cargo.width_cm, 6)
        parameters["height"] = round(cargo.height_cm, 6)
    return parameters


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def optional_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _modes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    response = payload.get("response")
    rates = response.get("estimatedFreightRates") if isinstance(response, dict) else None
    mode = rates.get("mode") if isinstance(rates, dict) else None
    if isinstance(mode, dict):
        return [mode]
    if isinstance(mode, list):
        return [item for item in mode if isinstance(item, dict)]
    return []


def parse_freightos_response(payload: Any, expected_mode: str) -> FreightosEstimate:
    if not isinstance(payload, dict):
        raise FreightosProviderError("Provider 返回的 JSON 结构无效", "invalid_response")
    modes = _modes(payload)
    selected = next(
        (item for item in modes if str(item.get("mode") or "").casefold() == expected_mode.casefold()),
        modes[0] if len(modes) == 1 else None,
    )
    if selected is None:
        raise FreightosProviderError("Provider 没有返回匹配的运输方式", "invalid_response")
    price = selected.get("price") if isinstance(selected.get("price"), dict) else {}
    minimum_money = price.get("min", {}).get("moneyAmount", {}) if isinstance(price.get("min"), dict) else {}
    maximum_money = price.get("max", {}).get("moneyAmount", {}) if isinstance(price.get("max"), dict) else {}
    minimum_currency = minimum_money.get("currency") if isinstance(minimum_money, dict) else None
    maximum_currency = maximum_money.get("currency") if isinstance(maximum_money, dict) else None
    currency = str(minimum_currency or maximum_currency).upper() if minimum_currency or maximum_currency else None
    if minimum_currency and maximum_currency and str(minimum_currency).upper() != str(maximum_currency).upper():
        raise FreightosProviderError("Provider 成本区间币种不一致", "invalid_response")
    transit = selected.get("transitTimes") if isinstance(selected.get("transitTimes"), dict) else {}
    cost_min = optional_number(minimum_money.get("amount") if isinstance(minimum_money, dict) else None)
    cost_max = optional_number(maximum_money.get("amount") if isinstance(maximum_money, dict) else None)
    transit_min = optional_number(transit.get("min"))
    transit_max = optional_number(transit.get("max"))
    if cost_min is not None and cost_max is not None and cost_min > cost_max:
        raise FreightosProviderError("Provider 成本区间上下限无效", "invalid_response")
    if transit_min is not None and transit_max is not None and transit_min > transit_max:
        raise FreightosProviderError("Provider 时效区间上下限无效", "invalid_response")
    if cost_min is None and cost_max is None and transit_min is None and transit_max is None:
        raise FreightosProviderError("Provider 未返回可用成本或时效区间", "invalid_response")
    return FreightosEstimate(
        currency=currency,
        cost_min=cost_min,
        cost_max=cost_max,
        transit_min_days=transit_min,
        transit_max_days=transit_max,
        response_sha256=canonical_sha256(payload),
    )


class FreightosPublicProvider:
    name = "Freightos"

    def __init__(
        self,
        settings: RouteEstimateSettings | None = None,
        client_factory: Callable[..., httpx.AsyncClient] = httpx.AsyncClient,
    ):
        self.settings = settings or RouteEstimateSettings()
        self.client_factory = client_factory

    async def estimate(self, parameters: dict[str, Any], request_id: str, fingerprint: str) -> FreightosEstimate:
        started = time.monotonic()
        last_error: Exception | None = None
        timeout = httpx.Timeout(self.settings.timeout_seconds)
        async with self.client_factory(timeout=timeout, follow_redirects=False) as client:
            for attempt in range(self.settings.max_retries + 1):
                try:
                    response = await client.get(FREIGHTOS_PUBLIC_ENDPOINT, params=parameters)
                    if response.status_code == 429:
                        retry_after = min(float(response.headers.get("Retry-After", "0") or 0), 30.0)
                        if attempt < self.settings.max_retries:
                            await asyncio.sleep(retry_after or 2**attempt + random.random())
                            continue
                    if response.status_code >= 500 and attempt < self.settings.max_retries:
                        await asyncio.sleep(2**attempt + random.random())
                        continue
                    response.raise_for_status()
                    content_length = response.headers.get("Content-Length")
                    try:
                        declared_size = int(content_length) if content_length is not None else None
                    except ValueError as exc:
                        raise FreightosProviderError("Provider Content-Length 无效", "invalid_response") from exc
                    if (declared_size is not None and declared_size > MAX_PROVIDER_RESPONSE_BYTES) or len(response.content) > MAX_PROVIDER_RESPONSE_BYTES:
                        raise FreightosProviderError("Provider 响应超过 1 MiB 安全上限", "invalid_response")
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise FreightosProviderError("Provider 返回非 JSON 响应", "invalid_response") from exc
                    expected_mode = str(parameters["mode"])
                    result = parse_freightos_response(payload, expected_mode)
                    logger.info(
                        "route_estimate request_id=%s provider=%s status=success elapsed_ms=%d cache=miss fingerprint=%s",
                        request_id,
                        self.name,
                        int((time.monotonic() - started) * 1000),
                        fingerprint[:16],
                    )
                    return result
                except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError, FreightosProviderError) as exc:
                    last_error = exc
                    if attempt < self.settings.max_retries and not isinstance(exc, FreightosProviderError):
                        await asyncio.sleep(2**attempt + random.random())
        logger.warning(
            "route_estimate request_id=%s provider=%s status=failed elapsed_ms=%d cache=miss fingerprint=%s error_type=%s",
            request_id,
            self.name,
            int((time.monotonic() - started) * 1000),
            fingerprint[:16],
            type(last_error).__name__,
        )
        outcome: ProviderOutcome
        if isinstance(last_error, FreightosProviderError):
            outcome = last_error.outcome
        elif isinstance(last_error, httpx.TimeoutException):
            outcome = "timeout"
        elif isinstance(last_error, httpx.NetworkError):
            outcome = "network"
        elif isinstance(last_error, httpx.HTTPStatusError):
            status_code = last_error.response.status_code
            outcome = "http_4xx" if 400 <= status_code < 500 else "http_5xx" if status_code >= 500 else "invalid_response"
        else:
            outcome = "unavailable"
        raise FreightosProviderError("Freightos 公共估算当前不可用", outcome) from last_error
