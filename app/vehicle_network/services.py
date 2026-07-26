from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.vehicle_network.core import load_carriers, load_rates, load_strategy
from app.vehicle_network.models import (
    LocationIngestRequest, LocationSummary, RouteGenerateRequest, RouteLegRecord, RouteRecord, SourceType,
)
from app.vehicle_network.providers.caac import CaacAirportProvider
from app.vehicle_network.providers.route_estimator import estimate_leg
from app.vehicle_network.providers.sample_registry import SampleRegistryProvider
from app.vehicle_network.repository import VehicleNetworkRepository
from app.vehicle_network.scoring import calculate_mode_risk, estimate_cost, rank_routes
from app.provider_risk import is_fresh


logger = logging.getLogger(__name__)


class LocationIngestionService:
    def __init__(self, repository: VehicleNetworkRepository | None = None):
        self.repository = repository or VehicleNetworkRepository()
        self.providers = [SampleRegistryProvider(), CaacAirportProvider()]

    async def ingest(self, request: LocationIngestRequest, trace_id: str) -> dict[str, Any]:
        self.repository.ensure_schema()
        job_id = self.repository.start_job("location_ingestion", trace_id)
        all_locations = []
        providers = []
        failures = []
        for provider in self.providers:
            try:
                rows = await provider.collect(request, trace_id)
                all_locations.extend(rows)
                providers.append({"provider": provider.name, "status": "success", "records": len(rows)})
            except Exception as exc:
                logger.exception("地点 Provider 失败 trace_id=%s provider=%s", trace_id, provider.name)
                failures.append({"provider": provider.name, "error": str(exc)})
        unique = {location.id: location for location in all_locations}
        count = self.repository.merge_locations(list(unique.values()), job_id)
        status = "partial_success" if failures and count else "failed" if failures else "success"
        result = {"job_id": job_id, "trace_id": trace_id, "status": status, "locations_merged": count, "providers": providers, "failures": failures}
        self.repository.finish_job(job_id, status, result)
        return result


class RouteGenerationService:
    def __init__(self, repository: VehicleNetworkRepository | None = None):
        self.repository = repository or VehicleNetworkRepository()

    COUNTRY_CODES = {
        "china": "CN", "united states": "US", "usa": "US", "germany": "DE", "netherlands": "NL",
        "france": "FR", "belgium": "BE", "spain": "ES", "italy": "IT", "poland": "PL",
        "kazakhstan": "KZ", "russia": "RU", "mexico": "MX", "canada": "CA", "brazil": "BR",
    }
    LANDMASSES = {
        "eurasia": {"CN", "DE", "NL", "FR", "BE", "ES", "IT", "PL", "KZ", "RU", "TR"},
        "north_america": {"US", "CA", "MX"},
        "south_america": {"BR", "AR", "CL", "PE"},
    }

    def _country_code(self, location: dict[str, Any]) -> str:
        code = str(location.get("country_code") or "").upper()
        if code:
            return code
        return self.COUNTRY_CODES.get(str(location.get("country") or "").casefold(), "")

    def _same_landmass(self, origin: dict[str, Any], destination: dict[str, Any]) -> bool:
        origin_code = self._country_code(origin)
        destination_code = self._country_code(destination)
        return any(origin_code in countries and destination_code in countries for countries in self.LANDMASSES.values())

    def _mode_candidates(self, origin: dict[str, Any], destination: dict[str, Any], request: RouteGenerateRequest) -> tuple[list[str], list[dict[str, str]]]:
        origin_labels = set(origin.get("labels", []))
        destination_labels = set(destination.get("labels", []))
        modes = []
        rejected = []
        if "Port" in origin_labels and "Port" in destination_labels:
            modes.append("sea")
        if "Airport" in origin_labels and "Airport" in destination_labels:
            modes.append("air")
        same_landmass = self._same_landmass(origin, destination)
        direct_distance = estimate_leg(origin, destination, "rail")["distance_km"]
        if request.allow_multimodal and same_landmass and direct_distance <= 12000:
            modes.append("rail")
        else:
            rejected.append({"mode": "rail", "reason": "起终点不在同一连续陆地区域，禁止生成跨大洋铁路"})
        if request.allow_multimodal and same_landmass and direct_distance <= 5000:
            modes.append("road")
        else:
            rejected.append({"mode": "road", "reason": "公路距离过长或起终点之间存在海洋阻隔"})
        feasible = list(dict.fromkeys(modes))
        if request.mode_preferences:
            requested = set(request.mode_preferences)
            rejected.extend({"mode": mode, "reason": "该运输方式不满足当前地点类型或地理可行性"} for mode in requested if mode not in feasible)
            feasible = [mode for mode in feasible if mode in requested]
        return feasible, rejected

    def _canonical_location_id(self, location: dict[str, Any], fallback: str) -> str:
        """将名称查询解析成可稳定写入关系的地点标识。"""
        return str(
            location.get("location_id")
            or location.get("unlocode")
            or location.get("code")
            or location.get("iata")
            or location.get("iata_code")
            or location.get("icao")
            or location.get("id")
            or fallback
        )

    def _location_summary(self, location: dict[str, Any], location_id: str) -> LocationSummary:
        labels = location.get("labels", [])
        kind = "port" if "Port" in labels else "airport" if "Airport" in labels else "factory" if "Factory" in labels else "terminal"
        name_en = location.get("name_en") or location.get("name")
        name_zh = location.get("name_zh")
        return LocationSummary(
            id=location_id, name=str(name_zh or name_en or location_id), name_zh=name_zh, name_en=name_en,
            kind=kind, city=location.get("city"), country=location.get("country"),
            country_code=self._country_code(location) or None, latitude=location.get("latitude"), longitude=location.get("longitude"),
        )

    def _risk_value(self, *values: Any) -> float | None:
        for value in values:
            if value is not None:
                number = float(value)
                return number * 100 if 0 <= number <= 1 else number
        return None

    def _provider_risk_value(
        self,
        origin: dict[str, Any],
        destination: dict[str, Any],
        *,
        value_fields: tuple[str, ...],
        provider_fields: tuple[str, ...],
        provider_markers: tuple[str, ...],
        observed_field: str | None = None,
        max_age_hours: float | None = None,
    ) -> float | None:
        values: list[float] = []
        for location in (origin, destination):
            provider_text = " ".join(str(location.get(field) or "") for field in provider_fields).casefold()
            if not any(marker in provider_text for marker in provider_markers):
                continue
            if observed_field and max_age_hours is not None and not is_fresh(
                location.get(observed_field),
                now=datetime.now(timezone.utc),
                max_age_hours=max_age_hours,
            ):
                continue
            value = self._risk_value(*(location.get(field) for field in value_fields))
            if value is not None:
                values.append(value)
        return max(values) if values else None

    def _mode_signals(self, mode: str, origin: dict[str, Any], destination: dict[str, Any]) -> dict[str, float | None]:
        news = self._provider_risk_value(
            origin,
            destination,
            value_fields=("news_risk_score",),
            provider_fields=("news_risk_provider", "news_source"),
            provider_markers=("gdelt",),
            observed_field="news_risk_updated_at",
            max_age_hours=6,
        )
        weather = self._provider_risk_value(
            origin,
            destination,
            value_fields=("weather_risk_score",),
            provider_fields=("weather_risk_provider", "weather_source"),
            provider_markers=("open-meteo", "open meteo"),
            observed_field="weather_updated_at",
            max_age_hours=6,
        )
        congestion = self._provider_risk_value(
            origin,
            destination,
            value_fields=("congestion_score", "congestionRisk"),
            provider_fields=("congestion_provider", "port_congestion_provider", "congestion_source"),
            provider_markers=("official", "port authority", "project44", "fourkites", "marinetraffic", "portcast"),
        )
        profiles = {
            "sea": {"weather": weather, "piracy": None, "port_congestion": congestion, "geopolitical": news, "sanctions": None, "schedule_reliability": None},
            "rail": {"border_customs": None, "geopolitical": news, "infrastructure": None, "weather": weather, "schedule_reliability": None, "sanctions": None},
            "road": {"traffic": None, "border_customs": None, "road_security": None, "weather": weather, "regulatory": None, "schedule_reliability": None},
            "air": {"weather": weather, "airspace_conflict": news, "airport_capacity": congestion, "schedule_reliability": None, "sanctions": None, "cargo_handling": None},
        }
        return profiles[mode]

    def generate(self, request: RouteGenerateRequest, trace_id: str) -> dict[str, Any]:
        self.repository.ensure_schema()
        job_id = self.repository.start_job("route_generation", trace_id)
        origin = self.repository.get_location(request.origin)
        destination = self.repository.get_location(request.destination)
        if not origin or not destination:
            missing = request.origin if not origin else request.destination
            self.repository.finish_job(job_id, "failed", {"error": f"地点不存在: {missing}"})
            raise ValueError(f"地点不存在: {missing}，请先执行地点采集或人工添加地点")
        required = ("latitude", "longitude")
        if any(origin.get(field) is None or destination.get(field) is None for field in required):
            self.repository.finish_job(job_id, "failed", {"error": "起点或终点缺少经纬度"})
            raise ValueError("起点或终点缺少经纬度，无法生成估算路线")

        origin_id = self._canonical_location_id(origin, request.origin)
        destination_id = self._canonical_location_id(destination, request.destination)
        origin_summary = self._location_summary(origin, origin_id)
        destination_summary = self._location_summary(destination, destination_id)

        strategy = load_strategy()
        rates = load_rates()
        carriers = load_carriers()
        modes, rejected_modes = self._mode_candidates(origin, destination, request)
        if not modes:
            self.repository.finish_job(job_id, "failed", {"error": "没有地理可行的运输方式", "rejected_modes": rejected_modes})
            raise ValueError(f"没有地理可行的运输方式: {rejected_modes}")
        routes: list[RouteRecord] = []
        for index, mode in enumerate(modes, start=1):
            estimate = estimate_leg(origin, destination, mode)
            confidence = 0.58 if mode in {"sea", "air"} else 0.42
            provenance = {
                "source": "图路径距离估算器", "source_url": None,
                "source_type": SourceType.ESTIMATED_BY_GRAPH, "confidence": confidence,
                "is_inferred": True, "review_status": "pending",
            }
            leg = RouteLegRecord(
                **provenance, leg_id=f"leg_{origin_id}_{destination_id}_{mode}_1".lower().replace("-", "_"),
                sequence=1, mode=mode, origin_id=origin_id, destination_id=destination_id,
                from_location=origin_summary, to_location=destination_summary,
                carrier_candidates=carriers.get(mode, []),
                carrier_display_name="候选承运人（待真实班期验证）",
                vessel_name_display="待 AIS 或船公司班期匹配" if mode == "sea" else "不适用",
                flight_number_display="待航班 Provider 匹配" if mode == "air" else "不适用",
                train_number_display="待铁路班列数据匹配" if mode == "rail" else "不适用",
                **estimate,
            )
            route_id = f"vehicle_route_{origin_id}_{destination_id}_{mode}_{index}".lower().replace("-", "_")
            route = RouteRecord(
                **provenance, route_id=route_id, route_type=mode, origin_id=origin_id,
                destination_id=destination_id, origin=origin_summary, destination=destination_summary, legs_count=1,
                estimated_distance_km=leg.distance_km, estimated_duration_h=leg.duration_h,
                evidence_count=0, historical_supported=False, needs_review=True, legs=[leg],
            )
            route.estimated_cost = estimate_cost(route.legs, rates)
            route.risk = calculate_mode_risk(mode, self._mode_signals(mode, origin, destination), strategy)
            routes.append(route)
        routes = rank_routes(routes, request.ranking_strategy, strategy)
        if request.persist:
            for route in routes:
                self.repository.merge_route(route, job_id)
        result = {
            "success": True, "job_id": job_id, "trace_id": trace_id,
            "query": {"origin": request.origin, "destination": request.destination, "resolved_origin_id": origin_id, "resolved_destination_id": destination_id, "ranking_strategy": request.ranking_strategy},
            "rejected_modes": rejected_modes,
            "routes": [route.model_dump(mode="json") for route in routes],
        }
        self.repository.finish_job(job_id, "success", {"routes_generated": len(routes), "persisted": request.persist})
        return result

    def recompute(self, route_id: str, trace_id: str) -> dict[str, Any]:
        stored = self.repository.get_route(route_id)
        if not stored:
            raise ValueError("路线不存在")
        route_data = stored["route"]
        request = RouteGenerateRequest(
            origin=route_data["origin_id"], destination=route_data["destination_id"],
            mode_preferences=[route_data["route_type"]], ranking_strategy="hybrid", persist=True,
        )
        result = self.generate(request, trace_id)
        return {"route_id": route_id, "status": "recomputed", "latest": result["routes"][0]}
