from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.route_estimates.config import RouteEstimateSettings
from app.route_estimates.freightos import (
    FreightosProviderError,
    FreightosPublicProvider,
    build_freightos_parameters,
)
from app.route_estimates.locations import LocationResolutionError, QueryFunction, resolve_port_unlocode
from app.route_estimates.models import (
    CostRange,
    EstimateStatus,
    RouteEstimateRequest,
    RouteEstimateResponse,
    ShipmentMethod,
    TransitTimeRange,
)
from app.route_estimates.telemetry import record_route_estimate_stage


logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    expires_monotonic: float
    response: RouteEstimateResponse


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def request_fingerprint(origin: str, destination: str, parameters: dict[str, Any]) -> str:
    payload = {"origin": origin, "destination": destination, "parameters": parameters}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RouteEstimateService:
    def __init__(
        self,
        query: QueryFunction,
        provider: FreightosPublicProvider | None = None,
        settings: RouteEstimateSettings | None = None,
    ):
        self.settings = settings or RouteEstimateSettings()
        self.provider = provider or FreightosPublicProvider(self.settings)
        self.query = query
        self.cache: dict[str, CacheEntry] = {}
        self.fingerprint_locks: dict[str, asyncio.Lock] = {}
        self.request_times: deque[float] = deque()
        self.failure_count = 0
        self.circuit_open_until = 0.0
        self.state_lock = asyncio.Lock()

    async def estimate(self, payload: RouteEstimateRequest) -> RouteEstimateResponse:
        request_id = uuid4().hex
        if payload.cargo.shipment_method == ShipmentMethod.RORO:
            record_route_estimate_stage("location_resolution", "not_reached")
            record_route_estimate_stage("provider_cache", "not_reached")
            record_route_estimate_stage("provider_call", "not_called")
            record_route_estimate_stage("provider_call", "unsupported")
            fingerprint = request_fingerprint(payload.origin_port_id, payload.destination_port_id, payload.model_dump(mode="json", by_alias=True))
            return self._empty_response(
                EstimateStatus.UNSUPPORTED,
                fingerprint,
                missing_data=["freightos_marketplace_roro"],
                assumptions=["Freightos 公共市场端点明确不支持 RoRo；请求未被改写为集装箱"],
            )
        try:
            origin = resolve_port_unlocode(payload.origin_port_id, self.query)
            destination = resolve_port_unlocode(payload.destination_port_id, self.query)
        except LocationResolutionError as exc:
            outcome = {
                "unknown_port": "unknown_port",
                "ambiguous_port": "ambiguous_port",
                "invalid_location_identity": "invalid_identity",
                "missing_canonical_unlocode": "missing_unlocode",
            }.get(exc.code, "unavailable")
            record_route_estimate_stage("location_resolution", outcome)
            raise
        except RuntimeError:
            record_route_estimate_stage("location_resolution", "unavailable")
            raise
        record_route_estimate_stage("location_resolution", "completed")
        parameters = build_freightos_parameters(origin, destination, payload.cargo)
        fingerprint = request_fingerprint(origin, destination, parameters)
        lock, expired_before_lock = await self._prepare_lock(fingerprint)
        async with lock:
            cached, expired_while_waiting = await self._cached(fingerprint)
            expired = expired_before_lock or expired_while_waiting
            if cached is not None:
                record_route_estimate_stage("provider_cache", "hit")
                record_route_estimate_stage("provider_call", "not_called")
                logger.info(
                    "route_estimate request_id=%s provider=Freightos status=%s elapsed_ms=0 cache=hit fingerprint=%s",
                    request_id,
                    cached.status,
                    fingerprint[:16],
                )
                return cached
            record_route_estimate_stage("provider_cache", "expired" if expired else "miss")
            if await self._circuit_is_open():
                record_route_estimate_stage("provider_call", "circuit_open")
                return self._empty_response(
                    EstimateStatus.EXPIRED if expired else EstimateStatus.UNAVAILABLE,
                    fingerprint,
                    cache_status="expired" if expired else "miss",
                    missing_data=["provider_circuit_open"],
                    assumptions=["Provider 熔断期间未返回或复用过期估算"],
                )
            if not await self._consume_rate_budget():
                record_route_estimate_stage("provider_call", "rate_limited")
                return self._empty_response(
                    EstimateStatus.EXPIRED if expired else EstimateStatus.UNAVAILABLE,
                    fingerprint,
                    cache_status="expired" if expired else "miss",
                    missing_data=["provider_hourly_rate_budget"],
                    assumptions=["已达到本进程每小时 100 次的公共端点保护上限"],
                )
            record_route_estimate_stage("provider_call", "called")
            try:
                estimate = await self.provider.estimate(parameters, request_id, fingerprint)
            except FreightosProviderError as exc:
                record_route_estimate_stage("provider_call", exc.outcome)
                await self._record_failure()
                return self._empty_response(
                    EstimateStatus.EXPIRED if expired else EstimateStatus.UNAVAILABLE,
                    fingerprint,
                    cache_status="expired" if expired else "miss",
                    missing_data=["provider_estimate"],
                    assumptions=["Provider 请求失败；未返回或复用过期估算"],
                )
            await self._record_success()
            now = datetime.now(timezone.utc)
            expires_at = now + timedelta(seconds=self.settings.cache_ttl_seconds)
            has_cost = estimate.cost_min is not None or estimate.cost_max is not None
            has_duration = estimate.transit_min_days is not None or estimate.transit_max_days is not None
            status = EstimateStatus.AVAILABLE if has_cost and has_duration else EstimateStatus.PARTIAL
            record_route_estimate_stage("provider_call", status.value)
            missing = []
            if not has_cost:
                missing.append("costRange")
            if not has_duration:
                missing.append("transitTimeRange")
            assumptions = [
                "Freightos 公共市场 Beta 数据仅为市场参考估算，不是承运人报价或运输承诺",
                "grossWeightKg 和 volumeM3 按总量接收，并按 quantity 换算为每个 load unit",
                "排序标量使用区间上限，不生成或暗示 mostLikely",
            ]
            response = RouteEstimateResponse(
                status=status,
                retrieved_at=utc_text(now),
                cache_expires_at=utc_text(expires_at),
                provider_valid_until=None,
                currency=estimate.currency,
                cost_range=CostRange(min=estimate.cost_min, max=estimate.cost_max),
                transit_time_range=TransitTimeRange(
                    min_days=estimate.transit_min_days,
                    max_days=estimate.transit_max_days,
                ),
                ranking_cost_usd=estimate.cost_max,
                ranking_duration_days=estimate.transit_max_days,
                request_fingerprint=fingerprint,
                response_sha256=estimate.response_sha256,
                missing_data=missing,
                assumptions=assumptions,
                cache_status="miss",
            )
            async with self.state_lock:
                self.cache[fingerprint] = CacheEntry(time.monotonic() + self.settings.cache_ttl_seconds, response)
            return response

    async def _prepare_lock(self, fingerprint: str) -> tuple[asyncio.Lock, bool]:
        async with self.state_lock:
            now = time.monotonic()
            expired_keys = {
                key for key, entry in self.cache.items() if entry.expires_monotonic <= now
            }
            for key in expired_keys:
                self.cache.pop(key, None)
            for key, existing_lock in list(self.fingerprint_locks.items()):
                if key not in self.cache and not existing_lock.locked():
                    self.fingerprint_locks.pop(key, None)
            while self.request_times and self.request_times[0] <= now - 3600:
                self.request_times.popleft()
            lock = self.fingerprint_locks.setdefault(fingerprint, asyncio.Lock())
            return lock, fingerprint in expired_keys

    async def _cached(self, fingerprint: str) -> tuple[RouteEstimateResponse | None, bool]:
        async with self.state_lock:
            entry = self.cache.get(fingerprint)
            if entry is None:
                return None, False
            if entry.expires_monotonic <= time.monotonic():
                self.cache.pop(fingerprint, None)
                return None, True
            return entry.response.model_copy(update={"cache_status": "hit"}, deep=True), False

    async def _consume_rate_budget(self) -> bool:
        async with self.state_lock:
            now = time.monotonic()
            while self.request_times and self.request_times[0] <= now - 3600:
                self.request_times.popleft()
            if len(self.request_times) >= self.settings.hourly_request_limit:
                return False
            self.request_times.append(now)
            return True

    async def _circuit_is_open(self) -> bool:
        async with self.state_lock:
            return self.circuit_open_until > time.monotonic()

    async def _record_failure(self) -> None:
        async with self.state_lock:
            self.failure_count += 1
            if self.failure_count >= self.settings.circuit_failure_threshold:
                self.circuit_open_until = time.monotonic() + self.settings.circuit_open_seconds

    async def _record_success(self) -> None:
        async with self.state_lock:
            self.failure_count = 0
            self.circuit_open_until = 0.0

    def _empty_response(
        self,
        status: EstimateStatus,
        fingerprint: str,
        *,
        cache_status: str = "miss",
        missing_data: list[str] | None = None,
        assumptions: list[str] | None = None,
    ) -> RouteEstimateResponse:
        return RouteEstimateResponse(
            status=status,
            retrieved_at=utc_text(datetime.now(timezone.utc)),
            cache_expires_at=None,
            provider_valid_until=None,
            currency=None,
            cost_range=CostRange(),
            transit_time_range=TransitTimeRange(),
            request_fingerprint=fingerprint,
            response_sha256=None,
            missing_data=missing_data or [],
            assumptions=assumptions or [],
            cache_status=cache_status,
        )
