from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from app.vehicle_network.core import load_rates, load_strategy
from app.vehicle_network.models import (
    LocationIngestRequest, Provenance, RouteGenerateRequest, RouteLegRecord, RouteRecord, SourceType,
)
from app.vehicle_network.providers.caac import CaacAirportProvider
from app.vehicle_network.providers.route_estimator import estimate_leg
from app.vehicle_network.providers.sample_registry import SampleRegistryProvider
from app.vehicle_network.repository import VehicleNetworkRepository
from app.vehicle_network.scoring import calculate_risk, estimate_cost, rank_routes


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

    def _mode_candidates(self, origin: dict[str, Any], destination: dict[str, Any], request: RouteGenerateRequest) -> list[str]:
        if request.mode_preferences:
            return request.mode_preferences
        origin_labels = set(origin.get("labels", []))
        destination_labels = set(destination.get("labels", []))
        modes = []
        if "Port" in origin_labels and "Port" in destination_labels:
            modes.append("sea")
        if "Airport" in origin_labels and "Airport" in destination_labels:
            modes.append("air")
        if request.allow_multimodal:
            modes.extend(["rail", "road"])
        return list(dict.fromkeys(modes or ["road"]))

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

        strategy = load_strategy()
        rates = load_rates()
        routes: list[RouteRecord] = []
        for index, mode in enumerate(self._mode_candidates(origin, destination, request), start=1):
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
                **estimate,
            )
            signals = {
                "news": float(origin.get("news_risk_score") or destination.get("news_risk_score") or 20),
                "weather": float(origin.get("weather_risk_score") or destination.get("weather_risk_score") or 20),
                "congestion": float(origin.get("congestion_score") or destination.get("congestion_score") or 25),
                "sanctions": 10, "schedule_reliability": 45 if mode in {"rail", "road"} else 30,
            }
            route_id = f"vehicle_route_{origin_id}_{destination_id}_{mode}_{index}".lower().replace("-", "_")
            route = RouteRecord(
                **provenance, route_id=route_id, route_type=mode, origin_id=origin_id,
                destination_id=destination_id, legs_count=1,
                estimated_distance_km=leg.distance_km, estimated_duration_h=leg.duration_h,
                evidence_count=0, historical_supported=False, needs_review=True, legs=[leg],
            )
            route.estimated_cost = estimate_cost(route.legs, rates)
            route.risk = calculate_risk(signals, strategy)
            routes.append(route)
        routes = rank_routes(routes, request.ranking_strategy, strategy)
        if request.persist:
            for route in routes:
                self.repository.merge_route(route, job_id)
        result = {
            "success": True, "job_id": job_id, "trace_id": trace_id,
            "query": {"origin": request.origin, "destination": request.destination, "resolved_origin_id": origin_id, "resolved_destination_id": destination_id, "ranking_strategy": request.ranking_strategy},
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
