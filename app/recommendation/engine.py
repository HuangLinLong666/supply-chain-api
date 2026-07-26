from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.recommendation.config import load_recommendation_settings
from app.recommendation.models import (
    RecommendationRequest,
    RecommendationStrategy,
    RecommendationWeights,
)
from app.route_optimizer import format_route, k_shortest_paths


VALID_MODES = {"road", "rail", "sea", "air"}
TRUSTED_COST_STATUSES = {"historical", "observed", "quoted", "contracted"}
TRUSTED_DURATION_STATUSES = {"historical", "observed"}


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def bounded(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return min(max(value, minimum), maximum)


def canonical_mode(value: Any) -> str | None:
    normalized = str(value or "").strip().casefold()
    if normalized in {"truck", "delivery"}:
        normalized = "road"
    return normalized if normalized in VALID_MODES else None


def first_positive(*values: Any) -> float | None:
    for value in values:
        number = optional_float(value)
        if number is not None and number > 0:
            return number
    return None


def parsed_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def route_signature(path: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(str(segment["segment_id"]) for segment in path)


def stable_route_id(path: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256("|".join(route_signature(path)).encode("utf-8")).hexdigest()[:20]
    return f"route-{digest}"


def timestamp_is_active(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed > datetime.now(timezone.utc)


class RecommendationEngine:
    def __init__(self, settings: dict[str, Any] | None = None):
        self.settings = settings or load_recommendation_settings()

    @property
    def scoring_version(self) -> str:
        return str(self.settings["scoring_version"])

    def resolved_weights(self, request: RecommendationRequest) -> RecommendationWeights:
        if request.weights is not None:
            return request.weights
        strategy = request.strategy.value
        configured = self.settings["strategy_weights"].get(strategy)
        if configured is None:
            raise ValueError(f"策略 {strategy} 没有默认权重")
        return RecommendationWeights.model_validate(configured)

    def normalization_metadata(self) -> dict[str, Any]:
        config = self.settings["normalization"]
        return {
            "method": config["method"],
            "candidateIndependent": True,
            "riskScore": config["risk_score"],
            "costPerVehicleUsd": config["cost_per_vehicle_usd"],
            "durationDays": config["duration_days"],
        }

    def utility_score(self, value: float | None, key: str) -> float | None:
        return self._utility_score(value, key)

    def penalty_score(self, value: float | None, key: str) -> float:
        return self._penalty_score(value, key)

    def prepare_segments(
        self,
        segments: list[dict[str, Any]],
        request: RecommendationRequest,
    ) -> list[dict[str, Any]]:
        allowed_modes = {mode.value for mode in request.constraints.allowed_modes}
        prepared: list[dict[str, Any]] = []
        for original in segments:
            mode = canonical_mode(original.get("canonical_mode") or original.get("mode"))
            if mode is None or mode not in allowed_modes:
                continue
            if str(original.get("feasibility_status") or "") == "invalid_cross_ocean":
                continue
            segment = dict(original)
            segment["mode"] = mode
            segment["canonical_mode"] = mode
            risk_providers = {
                str(provider).strip()
                for provider in segment.get("risk_providers") or []
                if str(provider).strip()
            }
            for details in parsed_mapping(segment.get("risk_breakdown")).values():
                factor = parsed_mapping(details)
                provider = str(factor.get("provider") or "").strip()
                if provider:
                    risk_providers.add(provider)
                risk_providers.update(
                    str(item).strip()
                    for item in factor.get("providers") or []
                    if str(item).strip()
                )
            if optional_float(segment.get("risk_score")) is not None and not risk_providers:
                segment["risk_score"] = None
                segment["risk_status"] = "unavailable"
                segment["risk_data_completeness"] = 0.0
                segment["risk_confidence"] = None
                segment["risk_missing_factors"] = sorted(
                    set(str(item) for item in segment.get("risk_missing_factors") or [])
                    | {"provider_backed_risk"}
                )
            segment["risk_providers"] = sorted(risk_providers)
            distance = first_positive(
                segment.get("geometry_distance_km"),
                segment.get("distance_km"),
            )
            segment["distance_km"] = distance
            cost_estimate = self._segment_cost_estimate(segment, request)
            duration_estimate = self._segment_duration_estimate(segment)
            segment["_cost_estimate"] = cost_estimate
            segment["_duration_estimate"] = duration_estimate
            segment["cost_usd"] = cost_estimate["most_likely"]
            segment["time_days"] = duration_estimate["duration_p50_days"]
            cost_per_vehicle = (
                cost_estimate["most_likely"] / request.cargo.quantity
                if cost_estimate["most_likely"] is not None
                else None
            )
            segment["cost_score"] = self._penalty_score(cost_per_vehicle, "cost_per_vehicle_usd")
            prepared.append(segment)
        return prepared

    def recommend(
        self,
        segments: list[dict[str, Any]],
        origin_ids: set[str],
        destination_ids: set[str],
        supplier: dict[str, Any],
        request: RecommendationRequest,
    ) -> dict[str, Any]:
        weights = self.resolved_weights(request)
        prepared = self.prepare_segments(segments, request)
        avoided_zone_ids = set(request.constraints.avoided_zone_ids)
        if avoided_zone_ids:
            prepared = [
                segment
                for segment in prepared
                if not avoided_zone_ids.intersection(self._segment_zone_ids(segment))
            ]

        baseline_paths = self._candidate_paths(prepared, origin_ids, destination_ids, request, weights)
        threshold = float(self.settings["candidate_generation"]["high_news_risk_threshold"])
        high_news_segments = {
            str(segment["segment_id"])
            for segment in prepared
            if float(segment.get("news_risk_score") or 0.0) >= threshold
        }
        safe_segments = [
            segment for segment in prepared if str(segment["segment_id"]) not in high_news_segments
        ]
        safe_paths = (
            self._candidate_paths(safe_segments, origin_ids, destination_ids, request, weights)
            if request.auto_reroute and high_news_segments
            else baseline_paths
        )
        fallback_used = bool(request.auto_reroute and high_news_segments and not safe_paths and baseline_paths)
        candidate_paths = baseline_paths if fallback_used else safe_paths
        if not candidate_paths:
            return {
                "networkPathFound": bool(baseline_paths),
                "candidateCount": 0,
                "eligibleCount": 0,
                "rejectedCandidates": [],
                "routes": [],
                "includedSegments": [],
                "dynamicRouting": {
                    "rerouted": False,
                    "avoidedZones": [],
                    "fallbackUsed": fallback_used,
                },
            }

        built_routes = [self._build_route(path, supplier, request) for path in candidate_paths]
        scored_routes = [self._score_route(route, weights, request) for route in built_routes]
        eligible: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for route in scored_routes:
            reasons = self._constraint_failures(route, request)
            if reasons:
                rejected.append({"routeId": route["id"], "reasons": reasons})
            else:
                eligible.append(route)

        eligible.sort(key=self._ranking_key)
        selected = eligible[: request.limit]
        self._decorate_rankings(selected, request)

        baseline_signature = route_signature(baseline_paths[0]) if baseline_paths else ()
        selected_signature = tuple(selected[0].pop("_segment_ids", [])) if selected else ()
        for route in selected[1:]:
            route.pop("_segment_ids", None)
        avoided_zones = self._high_risk_zones(baseline_paths[0], threshold) if baseline_paths else []
        rerouted = bool(
            selected
            and request.auto_reroute
            and avoided_zones
            and not fallback_used
            and baseline_signature != selected_signature
        )
        for route in selected:
            route["avoidedRiskZones"] = avoided_zones if rerouted else []

        inclusions: dict[str, list[str]] = defaultdict(list)
        for route in selected:
            for leg in route["legs"]:
                inclusions[str(leg["id"])].append(str(route["id"]))
        return {
            "networkPathFound": True,
            "candidateCount": len(candidate_paths),
            "eligibleCount": len(eligible),
            "rejectedCandidates": rejected[:50],
            "routes": selected,
            "includedSegments": [
                {"segment_id": segment_id, "route_ids": route_ids}
                for segment_id, route_ids in sorted(inclusions.items())
            ],
            "dynamicRouting": {
                "rerouted": rerouted,
                "avoidedZones": avoided_zones if rerouted else [],
                "fallbackUsed": fallback_used,
            },
        }

    def _candidate_paths(
        self,
        segments: list[dict[str, Any]],
        origin_ids: set[str],
        destination_ids: set[str],
        request: RecommendationRequest,
        weights: RecommendationWeights,
    ) -> list[list[dict[str, Any]]]:
        if not segments:
            return []
        configured_limit = int(self.settings["candidate_generation"]["per_objective_limit"])
        per_objective_limit = max(request.limit * 3, configured_limit)
        maximum_pool_size = int(self.settings["candidate_generation"]["maximum_pool_size"])
        primary = request.strategy.value
        if primary == RecommendationStrategy.CUSTOM.value:
            primary = RecommendationStrategy.BALANCED.value
        objectives = list(dict.fromkeys((primary, "min_risk", "min_cost", "fastest", "balanced")))
        risk_and_cost = weights.risk + weights.cost
        risk_weight = weights.risk / risk_and_cost if risk_and_cost else 0.5
        paths: list[list[dict[str, Any]]] = []
        signatures: set[tuple[str, ...]] = set()
        for objective in objectives:
            for path in k_shortest_paths(
                segments,
                origin_ids,
                destination_ids,
                objective,
                risk_weight,
                per_objective_limit,
                request.constraints.max_hops,
            ):
                signature = route_signature(path)
                if signature in signatures:
                    continue
                signatures.add(signature)
                paths.append(path)
                if len(paths) >= maximum_pool_size:
                    return paths
        return paths

    def _segment_cost_estimate(
        self,
        segment: dict[str, Any],
        request: RecommendationRequest,
    ) -> dict[str, Any]:
        observation = parsed_mapping(segment.get("cost_observation"))
        observation_status = str(
            observation.get("data_status") or observation.get("cost_status") or observation.get("status") or ""
        ).casefold()
        observation_amount = first_positive(observation.get("amount"), observation.get("metric_value"))
        observation_quantity = optional_float(observation.get("quantity"))
        provider = observation.get("provider") or observation.get("source")
        currency = str(observation.get("currency") or observation.get("unit") or "USD").upper()
        cargo_type_matches = not observation.get("cargo_type") or str(observation["cargo_type"]).casefold() == request.cargo.type.casefold()
        vehicle_type_matches = not observation.get("vehicle_type") or str(observation["vehicle_type"]).casefold() == str(request.cargo.vehicle_type or "").casefold()
        valid_until = observation.get("valid_until")
        valid_until_active = observation_status not in {"quoted", "contracted"} or (
            valid_until is not None and timestamp_is_active(valid_until)
        )
        can_use_observation = (
            observation_status in TRUSTED_COST_STATUSES
            and observation_amount is not None
            and provider
            and currency == "USD"
            and observation_quantity is not None
            and int(observation_quantity) == request.cargo.quantity
            and cargo_type_matches
            and vehicle_type_matches
            and valid_until_active
        )
        if can_use_observation:
            confidence = bounded(float(observation.get("confidence") or observation.get("confidence_score") or 0.7))
            return {
                "currency": currency,
                "min": round(first_positive(observation.get("min")) or observation_amount, 2),
                "most_likely": round(observation_amount, 2),
                "max": round(first_positive(observation.get("max")) or observation_amount, 2),
                "data_status": observation_status,
                "provider": str(provider),
                "confidence": confidence,
                "formula": "使用数量与本次请求完全一致且带 Provider 的成本观测",
                "cost_components": {"provider_total": round(observation_amount, 2)},
                "missing_components": [],
                "assumptions": [],
                "input_snapshot": {
                    "observationId": observation.get("observation_id"),
                    "quantity": request.cargo.quantity,
                },
            }

        model = self.settings["cost_model"]
        distance = optional_float(segment.get("distance_km"))
        mode = str(segment["mode"])
        if distance is None:
            return {
                "currency": str(model.get("currency", "USD")),
                "min": None,
                "most_likely": None,
                "max": None,
                "data_status": "unavailable",
                "provider": None,
                "confidence": 0.0,
                "formula": "缺少可用距离，无法运行内部估算模型",
                "cost_components": {},
                "missing_components": ["movement", "fuel", "handling", "insurance", "tariff"],
                "assumptions": [],
                "input_snapshot": {"distanceKm": None, "mode": mode},
            }
        rate = float(model["mode_rates_per_km"][mode])
        fuel_ratio = float(model["fuel_surcharge_ratio"].get(mode, 0.0))
        quantity = request.cargo.quantity
        shipment_method = str(request.cargo.shipment_method or "").strip().casefold()
        container_type = str(request.cargo.container_type or "").strip().casefold()
        shipment_multiplier = (
            float(model["shipment_method_rate_multiplier"].get(shipment_method, 1.0))
            if mode == "sea"
            else 1.0
        )
        container_multiplier = (
            float(model["container_type_rate_multiplier"].get(container_type, 1.0))
            if mode == "sea"
            else 1.0
        )
        unit_weight = request.cargo.gross_weight_kg / quantity if request.cargo.gross_weight_kg else None
        weight_multiplier = (
            max(unit_weight / float(model["reference_vehicle_weight_kg"]), 0.1)
            ** float(model["weight_multiplier_exponent"])
            if unit_weight is not None
            else 1.0
        )
        dimensions = (
            request.cargo.vehicle_length_m,
            request.cargo.vehicle_width_m,
            request.cargo.vehicle_height_m,
        )
        vehicle_volume = math.prod(float(value) for value in dimensions) if all(value is not None for value in dimensions) else None
        volume_multiplier = (
            max(vehicle_volume / float(model["reference_vehicle_volume_m3"]), 0.1)
            ** float(model["volume_multiplier_exponent"])
            if vehicle_volume is not None
            else 1.0
        )
        movement = distance * rate * quantity * shipment_multiplier * container_multiplier * weight_multiplier * volume_multiplier
        fuel = movement * fuel_ratio
        handling_key = "airport" if mode == "air" else "port" if mode == "sea" else "rail_terminal" if mode == "rail" else "road_terminal"
        capacity = int(model["mode_capacity"].get(mode, 1))
        if mode == "sea" and shipment_method:
            capacity = int(model["sea_capacity_by_shipment_method"].get(shipment_method, capacity))
        if mode == "sea" and shipment_method == "container" and container_type:
            capacity = int(model["container_capacity"].get(container_type, capacity))
        capacity = max(capacity, 1)
        handling_units = math.ceil(quantity / capacity)
        handling = float(model["handling_fees"].get(handling_key, 0.0)) * handling_units
        ev_ratio = (
            float(model.get("electric_vehicle_surcharge_ratio", 0.0))
            if str(request.cargo.vehicle_type or "").casefold() == "electric_vehicle"
            else 0.0
        )
        electric_vehicle_surcharge = (movement + fuel + handling) * ev_ratio
        tariff = (movement + fuel + handling + electric_vehicle_surcharge) * float(model.get("optional_tariff_rate", 0.0))
        total = movement + fuel + handling + electric_vehicle_surcharge + tariff
        uncertainty = model["uncertainty"]
        source_status = str(segment.get("data_status") or "estimated").casefold()
        confidence_key = "synthetic_confidence" if source_status == "synthetic" else "estimated_confidence"
        confidence = bounded(float(model[confidence_key]))
        assumptions = [
            "按每车公里费率估算，不是承运人实时报价",
            f"{mode} 装卸计费容量按每单元 {capacity} 辆估算",
            "保险、实际关税、港杂费和中转附加费暂无 Provider，未计入最可能值",
        ]
        if ev_ratio:
            assumptions.append(f"电动车运输限制使用 {ev_ratio:.0%} 内部估算附加比例")
        if shipment_method:
            assumptions.append(f"海运方式 {shipment_method} 使用内部费率系数 {shipment_multiplier:.3f}")
        if container_type:
            assumptions.append(f"箱型 {container_type} 使用内部费率系数 {container_multiplier:.3f}")
        if unit_weight is not None:
            assumptions.append(f"按单车估算重量 {unit_weight:.1f} kg 应用重量系数 {weight_multiplier:.3f}")
        if vehicle_volume is not None:
            assumptions.append(f"按单车体积 {vehicle_volume:.2f} m³ 应用体积系数 {volume_multiplier:.3f}")
        if observation_amount is not None:
            assumptions.append("已有成本观测因状态、Provider 或数量口径不满足要求而未采用")
        return {
            "currency": str(model.get("currency", "USD")),
            "min": round(total * float(uncertainty["min_ratio"]), 2),
            "most_likely": round(total, 2),
            "max": round(total * float(uncertainty["max_ratio"]), 2),
            "data_status": "estimated",
            "provider": None,
            "confidence": confidence,
            "formula": "距离×模式每车公里费率×数量×装载/重量/体积系数 + 燃油附加费 + 装卸费 + 电动车估算附加费 + 可选关税",
            "cost_components": {
                "movement": round(movement, 2),
                "fuel": round(fuel, 2),
                "handling": round(handling, 2),
                "electric_vehicle_surcharge": round(electric_vehicle_surcharge, 2),
                "tariff": round(tariff, 2),
                "insurance": None,
                "transfer": None,
            },
            "missing_components": ["insurance", "provider_tariff", "provider_transfer_fee"],
            "assumptions": assumptions,
            "input_snapshot": {
                "distanceKm": round(distance, 3),
                "mode": mode,
                "quantity": quantity,
                "vehicleType": request.cargo.vehicle_type,
                "shipmentMethod": request.cargo.shipment_method,
                "containerType": request.cargo.container_type,
                "unitWeightKg": round(unit_weight, 3) if unit_weight is not None else None,
                "vehicleVolumeM3": round(vehicle_volume, 3) if vehicle_volume is not None else None,
                "ratePerVehicleKm": rate,
                "shipmentMultiplier": shipment_multiplier,
                "containerMultiplier": container_multiplier,
                "weightMultiplier": round(weight_multiplier, 6),
                "volumeMultiplier": round(volume_multiplier, 6),
                "fuelSurchargeRatio": fuel_ratio,
                "handlingUnits": handling_units,
                "sourceDataStatus": source_status,
            },
        }

    def _segment_duration_estimate(self, segment: dict[str, Any]) -> dict[str, Any]:
        observation = parsed_mapping(segment.get("delay_observation"))
        observation_status = str(
            observation.get("data_status") or observation.get("duration_data_status") or observation.get("status") or ""
        ).casefold()
        provider = observation.get("provider") or observation.get("source")
        observed_p50 = first_positive(
            observation.get("duration_p50_days"),
            observation.get("total_duration_days"),
            observation.get("metric_value"),
        )
        if observation_status in TRUSTED_DURATION_STATUSES and provider and observed_p50 is not None:
            p90 = first_positive(observation.get("duration_p90_days")) or observed_p50
            confidence = bounded(float(observation.get("confidence") or observation.get("confidence_score") or 0.7))
            return {
                "movement_duration_days": optional_float(observation.get("movement_duration_days")),
                "waiting_duration_days": optional_float(observation.get("waiting_duration_days")),
                "customs_duration_days": optional_float(observation.get("customs_duration_days")),
                "transfer_duration_days": optional_float(observation.get("transfer_duration_days")),
                "expected_delay_days": optional_float(observation.get("expected_delay_days")),
                "total_duration_days": round(observed_p50, 3),
                "duration_p50_days": round(observed_p50, 3),
                "duration_p90_days": round(max(p90, observed_p50), 3),
                "data_status": observation_status,
                "confidence": confidence,
                "assumptions": [],
            }

        model = self.settings["duration_model"]
        mode = str(segment["mode"])
        distance = optional_float(segment.get("distance_km"))
        existing_days = first_positive(segment.get("raw_time_days"), segment.get("time_days"))
        movement_days: float | None
        if existing_days is not None:
            p50 = existing_days
            movement_days = None
            basis = "使用图中已有但未由真实班期 Provider 验证的时效估算"
        elif distance is not None:
            movement_days = distance / float(model["speed_kmh"][mode]) / 24.0
            p50 = movement_days + float(model["terminal_handling_hours"][mode]) / 24.0
            basis = "使用距离、模式平均速度和场站处理时间估算"
        else:
            return {
                "movement_duration_days": None,
                "waiting_duration_days": None,
                "customs_duration_days": None,
                "transfer_duration_days": None,
                "expected_delay_days": None,
                "total_duration_days": None,
                "duration_p50_days": None,
                "duration_p90_days": None,
                "data_status": "unavailable",
                "confidence": 0.0,
                "assumptions": ["缺少可用距离和时效观测"],
            }
        p90 = p50 * float(model["p90_multiplier"][mode])
        source_status = str(segment.get("data_status") or "estimated").casefold()
        confidence_key = "synthetic_confidence" if source_status == "synthetic" else "estimated_confidence"
        confidence = bounded(float(model[confidence_key]))
        assumptions = [
            basis,
            "等待、海关和中转时长没有真实 Provider，分别返回 null",
            "P90 是版本化不确定性倍数估算，不是班期承诺",
        ]
        if observed_p50 is not None:
            assumptions.append("已有时效观测因状态或 Provider 不满足要求而未采用")
        return {
            "movement_duration_days": round(movement_days, 3) if movement_days is not None else None,
            "waiting_duration_days": None,
            "customs_duration_days": None,
            "transfer_duration_days": None,
            "expected_delay_days": round(max(p90 - p50, 0.0), 3),
            "total_duration_days": round(p50, 3),
            "duration_p50_days": round(p50, 3),
            "duration_p90_days": round(p90, 3),
            "data_status": "estimated",
            "confidence": confidence,
            "assumptions": assumptions,
        }

    def _build_route(
        self,
        path: list[dict[str, Any]],
        supplier: dict[str, Any],
        request: RecommendationRequest,
    ) -> dict[str, Any]:
        route = format_route(path, 1)
        route["id"] = stable_route_id(path)
        route["name"] = self._route_name(path)
        route["_segment_ids"] = list(route_signature(path))
        self._apply_supplier_risk(route, supplier)
        route_cost = self._aggregate_cost(path, request)
        route_duration = self._aggregate_duration(path)
        route["costEstimate"] = route_cost
        route["durationEstimate"] = route_duration
        route["cost"] = route_cost["most_likely"]
        route["durationDays"] = route_duration["duration_p50_days"]
        route["distanceKm"] = round(sum(float(segment.get("distance_km") or 0.0) for segment in path), 2)
        for segment, leg in zip(path, route["legs"], strict=True):
            leg["id"] = str(segment["segment_id"])
            leg["mode"] = str(segment["mode"])
            leg["cost"] = segment["_cost_estimate"]["most_likely"]
            leg["durationDays"] = segment["_duration_estimate"]["duration_p50_days"]
            leg["costEstimate"] = segment["_cost_estimate"]
            leg["durationEstimate"] = segment["_duration_estimate"]
        self._complete_risk_factor_metadata(route, path)
        route["missingData"] = self._missing_data(route)
        route["estimatedFields"] = self._estimated_fields(route)
        route["avoidedRiskZones"] = []
        return route

    def _aggregate_cost(
        self,
        path: list[dict[str, Any]],
        request: RecommendationRequest,
    ) -> dict[str, Any]:
        estimates = [segment["_cost_estimate"] for segment in path]
        unavailable = any(estimate["most_likely"] is None for estimate in estimates)
        component_totals: dict[str, float | None] = {}
        component_keys = sorted({key for estimate in estimates for key in estimate["cost_components"]})
        for key in component_keys:
            values = [estimate["cost_components"].get(key) for estimate in estimates]
            component_totals[key] = round(sum(float(value) for value in values if value is not None), 2) if any(value is not None for value in values) else None
        statuses = {str(estimate["data_status"]) for estimate in estimates}
        data_status = "unavailable" if unavailable else next(iter(statuses)) if len(statuses) == 1 else "estimated"
        providers = sorted({str(estimate["provider"]) for estimate in estimates if estimate.get("provider")})
        return {
            "currency": "USD",
            "min": None if unavailable else round(sum(float(estimate["min"]) for estimate in estimates), 2),
            "most_likely": None if unavailable else round(sum(float(estimate["most_likely"]) for estimate in estimates), 2),
            "max": None if unavailable else round(sum(float(estimate["max"]) for estimate in estimates), 2),
            "data_status": data_status,
            "provider": providers[0] if len(providers) == 1 else None,
            "confidence": round(min(float(estimate["confidence"]) for estimate in estimates), 4),
            "formula": "逐段成本区间求和；内部 fallback 按每车公里费率和请求数量计算",
            "cost_components": component_totals,
            "missing_components": sorted({item for estimate in estimates for item in estimate["missing_components"]}),
            "assumptions": list(dict.fromkeys(item for estimate in estimates for item in estimate["assumptions"])),
            "input_snapshot": {
                "cargo": request.cargo.model_dump(mode="json", by_alias=True),
                "segmentIds": list(route_signature(path)),
                "segmentInputs": [estimate["input_snapshot"] for estimate in estimates],
            },
        }

    def _aggregate_duration(self, path: list[dict[str, Any]]) -> dict[str, Any]:
        estimates = [segment["_duration_estimate"] for segment in path]
        unavailable = any(estimate["duration_p50_days"] is None for estimate in estimates)
        statuses = {str(estimate["data_status"]) for estimate in estimates}
        data_status = "unavailable" if unavailable else next(iter(statuses)) if len(statuses) == 1 else "estimated"

        def total_if_known(key: str) -> float | None:
            values = [estimate.get(key) for estimate in estimates]
            if any(value is None for value in values):
                return None
            return round(sum(float(value) for value in values), 3)

        p50 = None if unavailable else round(sum(float(estimate["duration_p50_days"]) for estimate in estimates), 3)
        p90 = None if unavailable else round(sum(float(estimate["duration_p90_days"]) for estimate in estimates), 3)
        return {
            "movement_duration_days": total_if_known("movement_duration_days"),
            "waiting_duration_days": total_if_known("waiting_duration_days"),
            "customs_duration_days": total_if_known("customs_duration_days"),
            "transfer_duration_days": total_if_known("transfer_duration_days"),
            "expected_delay_days": total_if_known("expected_delay_days"),
            "total_duration_days": p50,
            "duration_p50_days": p50,
            "duration_p90_days": p90,
            "data_status": data_status,
            "confidence": round(min(float(estimate["confidence"]) for estimate in estimates), 4),
            "assumptions": list(dict.fromkeys(item for estimate in estimates for item in estimate["assumptions"])),
        }

    def _apply_supplier_risk(self, route: dict[str, Any], supplier: dict[str, Any]) -> None:
        provider_list = [str(item) for item in supplier.get("riskProviders") or [] if item]
        supplier_score = optional_float(supplier.get("riskScore")) if provider_list else None
        if supplier_score is not None and supplier_score <= 1.0:
            supplier_score *= 100.0
        supplier_completeness = bounded(float(supplier.get("riskDataCompleteness") or 0.0)) if supplier_score is not None else 0.0
        route_score = optional_float(route.get("riskScore"))
        route_completeness = bounded(float(route.get("riskDataCompleteness") or 0.0))
        if supplier_score is not None and route_score is not None:
            route["riskScore"] = round(0.2 * supplier_score + 0.8 * route_score, 2)
            route["riskDataCompleteness"] = round(0.2 * supplier_completeness + 0.8 * route_completeness, 4)
            route["riskStatus"] = "available" if supplier.get("riskStatus") == "available" and route.get("riskStatus") == "available" else "partial"
        elif route_score is not None:
            route["riskDataCompleteness"] = round(0.8 * route_completeness, 4)
            route["riskStatus"] = "partial"
            route["riskMissingFactors"] = sorted(set(route.get("riskMissingFactors") or []) | {"supplier_risk"})
        elif supplier_score is not None:
            route["riskScore"] = round(supplier_score, 2)
            route["riskDataCompleteness"] = round(0.2 * supplier_completeness, 4)
            route["riskStatus"] = "partial"
            route["riskMissingFactors"] = sorted(set(route.get("riskMissingFactors") or []) | {"route_risk"})
        else:
            route["riskScore"] = None
            route["riskDataCompleteness"] = 0.0
            route["riskStatus"] = "unavailable"
            route["riskMissingFactors"] = sorted(set(route.get("riskMissingFactors") or []) | {"supplier_risk", "route_risk"})
        route["riskProviders"] = sorted(set(route.get("riskProviders") or []) | set(provider_list))
        route["riskFactors"].insert(
            0,
            {
                "key": "supplier",
                "label": "供应商",
                "score": round(supplier_score, 2) if supplier_score is not None else None,
                "status": supplier.get("riskStatus") if supplier_score is not None else "unavailable",
                "provider": provider_list[0] if provider_list else None,
                "providers": provider_list,
                "confidence": supplier_completeness if supplier_score is not None else 0.0,
                "affectedLegIds": [],
                "evidence": supplier.get("riskEvidence") or [],
                "detail": supplier.get("riskExplanation") or "供应商暂无可验证风险 Provider",
            },
        )

    def _complete_risk_factor_metadata(self, route: dict[str, Any], path: list[dict[str, Any]]) -> None:
        for factor in route["riskFactors"]:
            if factor["key"] == "supplier":
                continue
            keys = {factor["key"], f"{factor['key']}_risk"}
            affected: list[str] = []
            confidences: list[float] = []
            for segment in path:
                breakdown = parsed_mapping(segment.get("risk_breakdown"))
                if any(
                    key in breakdown
                    and parsed_mapping(breakdown.get(key)).get("value") is not None
                    and (
                        parsed_mapping(breakdown.get(key)).get("provider")
                        or parsed_mapping(breakdown.get(key)).get("providers")
                    )
                    for key in keys
                ):
                    affected.append(str(segment["segment_id"]))
                    confidence = optional_float(segment.get("risk_confidence"))
                    if confidence is not None:
                        confidences.append(confidence)
            factor["affectedLegIds"] = affected
            factor["confidence"] = round(sum(confidences) / len(confidences), 4) if confidences else None
            factor.setdefault("observedAt", None)
            factor.setdefault("expiresAt", None)

    def _score_route(
        self,
        route: dict[str, Any],
        weights: RecommendationWeights,
        request: RecommendationRequest,
    ) -> dict[str, Any]:
        risk_score = optional_float(route.get("riskScore"))
        cost = optional_float(route["costEstimate"].get("most_likely"))
        duration = optional_float(route["durationEstimate"].get("duration_p50_days"))
        sub_scores = {
            "risk": self._utility_score(risk_score, "risk_score"),
            "cost": self._utility_score(cost / request.cargo.quantity if cost is not None else None, "cost_per_vehicle_usd"),
            "duration": self._utility_score(duration, "duration_days"),
        }
        confidences = {
            "risk": bounded(float(route.get("riskDataCompleteness") or 0.0)) if risk_score is not None else 0.0,
            "cost": bounded(float(route["costEstimate"].get("confidence") or 0.0)) if cost is not None else 0.0,
            "duration": bounded(float(route["durationEstimate"].get("confidence") or 0.0)) if duration is not None else 0.0,
        }
        weight_values = weights.model_dump()
        contributions = {
            key: round(float(weight_values[key]) * float(sub_scores[key] or 0.0), 4)
            for key in ("risk", "cost", "duration")
        }
        base_score = sum(contributions.values())
        uncertainty_weight = float(self.settings["uncertainty"]["penalty_weight"])
        uncertainty_penalty = 100.0 * uncertainty_weight * sum(
            float(weight_values[key]) * (1.0 - confidences[key])
            for key in ("risk", "cost", "duration")
        )
        final_score = bounded(base_score - uncertainty_penalty, 0.0, 100.0)
        completeness = sum(float(weight_values[key]) * confidences[key] for key in ("risk", "cost", "duration"))
        route["scoreBreakdown"] = {
            "weights": weights.model_dump(mode="json", by_alias=True),
            "subScores": sub_scores,
            "weightedContributions": contributions,
            "baseScore": round(base_score, 4),
            "uncertaintyPenalty": round(uncertainty_penalty, 4),
            "finalScore": round(final_score, 4),
            "dataCompleteness": round(completeness, 4),
        }
        route["finalScore"] = round(final_score, 4)
        route["uncertaintyPenalty"] = round(uncertainty_penalty, 4)
        route["dataCompleteness"] = round(completeness, 4)
        return route

    def _constraint_failures(self, route: dict[str, Any], request: RecommendationRequest) -> list[str]:
        constraints = request.constraints
        reasons: list[str] = []
        modes = {str(leg["mode"]) for leg in route["legs"]}
        allowed = {mode.value for mode in constraints.allowed_modes}
        if not modes.issubset(allowed):
            reasons.append(f"包含 allowedModes 之外的运输方式: {sorted(modes - allowed)}")
        risk_score = optional_float(route.get("riskScore"))
        if constraints.require_known_risk and risk_score is None:
            reasons.append("requireKnownRisk=true，但路线没有可验证风险数据")
        if constraints.max_risk_score is not None:
            if risk_score is None:
                reasons.append("maxRiskScore 无法验证：路线风险数据不可用")
            elif risk_score > constraints.max_risk_score:
                reasons.append(f"riskScore {risk_score:.2f} > {constraints.max_risk_score:.2f}")
        cost = optional_float(route["costEstimate"].get("most_likely"))
        if constraints.max_cost_usd is not None:
            if cost is None:
                reasons.append("maxCostUsd 无法验证：成本不可用")
            elif cost > constraints.max_cost_usd:
                reasons.append(f"cost {cost:.2f} USD > {constraints.max_cost_usd:.2f} USD")
        duration_p90 = optional_float(route["durationEstimate"].get("duration_p90_days"))
        if constraints.max_duration_days is not None:
            if duration_p90 is None:
                reasons.append("maxDurationDays 无法验证：P90 时效不可用")
            elif duration_p90 > constraints.max_duration_days:
                reasons.append(f"durationP90Days {duration_p90:.2f} > {constraints.max_duration_days:.2f}")
        if constraints.min_data_completeness is not None and float(route["dataCompleteness"]) < constraints.min_data_completeness:
            reasons.append(
                f"dataCompleteness {route['dataCompleteness']:.4f} < {constraints.min_data_completeness:.4f}"
            )
        return reasons

    def _ranking_key(self, route: dict[str, Any]) -> tuple[Any, ...]:
        risk = optional_float(route.get("riskScore"))
        cost = optional_float(route.get("cost"))
        duration = optional_float(route.get("durationDays"))
        return (
            -float(route["finalScore"]),
            risk is None,
            risk if risk is not None else float("inf"),
            cost if cost is not None else float("inf"),
            duration if duration is not None else float("inf"),
            str(route["id"]),
        )

    def _decorate_rankings(self, routes: list[dict[str, Any]], request: RecommendationRequest) -> None:
        if not routes:
            return
        for index, route in enumerate(routes, start=1):
            route["rank"] = index
            route["whyRecommended"] = [
                f"按 {request.strategy.value} 策略排名第 {index}",
                f"固定锚点基础分 {route['scoreBreakdown']['baseScore']:.2f}，不确定性扣分 {route['uncertaintyPenalty']:.2f}",
                f"最终综合分 {route['finalScore']:.2f}/100，数据完整度 {route['dataCompleteness']:.1%}",
            ]
            if route.get("riskScore") is None:
                route["whyRecommended"].append("风险观测不可用，未填入 50 分；风险权重没有获得效用分并单独扣除不确定性")
            else:
                route["whyRecommended"].append(f"可用风险观测聚合分 {route['riskScore']:.2f}/100")
            route["whyRecommended"].append(
                f"成本 {route['cost']:.2f} USD（{route['costEstimate']['data_status']}），P50 时效 {route['durationDays']:.2f} 天"
            )
        known_cost = [route for route in routes if route.get("cost") is not None]
        known_risk = [route for route in routes if route.get("riskScore") is not None]
        known_duration = [route for route in routes if route.get("durationDays") is not None]
        if known_cost:
            min(known_cost, key=lambda item: item["cost"])["tags"].insert(0, "成本最优")
        if known_risk:
            min(known_risk, key=lambda item: item["riskScore"])["tags"].insert(0, "风险最优")
        if known_duration:
            min(known_duration, key=lambda item: item["durationDays"])["tags"].insert(0, "时效最优")
        for current, next_route in zip(routes, routes[1:]):
            comparison = {
                "comparedWithRouteId": next_route["id"],
                "riskScoreDelta": self._difference(current.get("riskScore"), next_route.get("riskScore")),
                "costUsdDelta": self._difference(current.get("cost"), next_route.get("cost")),
                "durationDaysDelta": self._difference(current.get("durationDays"), next_route.get("durationDays")),
            }
            current["comparisonToNext"] = comparison
            if current["rank"] == 1:
                self._add_comparison_explanation(current, comparison)
        routes[-1]["comparisonToNext"] = None

    def _add_comparison_explanation(self, route: dict[str, Any], comparison: dict[str, Any]) -> None:
        risk_delta = comparison["riskScoreDelta"]
        cost_delta = comparison["costUsdDelta"]
        duration_delta = comparison["durationDaysDelta"]
        if risk_delta is not None:
            wording = "低" if risk_delta < 0 else "高"
            route["whyRecommended"].append(f"相较第二名风险{wording} {abs(risk_delta):.2f} 分")
        if cost_delta is not None:
            wording = "低" if cost_delta < 0 else "高"
            route["whyRecommended"].append(f"相较第二名成本{wording} {abs(cost_delta):.2f} USD")
        if duration_delta is not None:
            wording = "短" if duration_delta < 0 else "长"
            route["whyRecommended"].append(f"相较第二名 P50 时效{wording} {abs(duration_delta):.2f} 天")

    def _utility_score(self, value: float | None, key: str) -> float | None:
        if value is None:
            return None
        bounds = self.settings["normalization"][key]
        best = float(bounds["best"])
        worst = float(bounds["worst"])
        if worst <= best:
            raise ValueError(f"归一化范围 {key} 配置无效")
        clipped = min(max(value, best), worst)
        return round((worst - clipped) / (worst - best) * 100.0, 4)

    def _penalty_score(self, value: float | None, key: str) -> float:
        utility = self._utility_score(value, key)
        return round(1.0 - (utility or 0.0) / 100.0, 6)

    def _route_name(self, path: list[dict[str, Any]]) -> str:
        modes = list(dict.fromkeys(str(segment["mode"]) for segment in path))
        mode_names = {"road": "公路", "rail": "铁路", "sea": "海运", "air": "空运"}
        origin = str(path[0].get("from_name") or "起点")
        destination = str(path[-1].get("to_name") or "终点")
        return f"{origin} → {destination}（{' + '.join(mode_names[mode] for mode in modes)}）"

    def _missing_data(self, route: dict[str, Any]) -> list[str]:
        missing = set(str(item) for item in route.get("riskMissingFactors") or [])
        if route.get("riskScore") is None:
            missing.add("risk_score")
        missing.update(f"cost:{item}" for item in route["costEstimate"]["missing_components"])
        for key in ("waiting_duration_days", "customs_duration_days", "transfer_duration_days"):
            if route["durationEstimate"].get(key) is None:
                missing.add(f"duration:{key}")
        return sorted(missing)

    def _estimated_fields(self, route: dict[str, Any]) -> list[str]:
        fields: list[str] = []
        if route["costEstimate"]["data_status"] == "estimated":
            fields.append("cost")
        if route["durationEstimate"]["data_status"] == "estimated":
            fields.extend(["durationP50Days", "durationP90Days"])
        if any(str(leg.get("geometryStatus") or "").startswith("estimated") for leg in route["legs"]):
            fields.append("geometry")
        return fields

    def _segment_zone_ids(self, segment: dict[str, Any]) -> set[str]:
        return {
            str(exposure.get("zoneId") or exposure.get("zone_id"))
            for exposure in segment.get("spatial_exposures") or []
            if exposure.get("zoneId") or exposure.get("zone_id")
        }

    def _high_risk_zones(self, path: list[dict[str, Any]], threshold: float) -> list[str]:
        return sorted(
            {
                str(zone)
                for segment in path
                if float(segment.get("news_risk_score") or 0.0) >= threshold
                for zone in segment.get("news_risk_zones") or []
            }
        )

    @staticmethod
    def _difference(first: Any, second: Any) -> float | None:
        first_value = optional_float(first)
        second_value = optional_float(second)
        if first_value is None or second_value is None:
            return None
        return round(first_value - second_value, 4)
