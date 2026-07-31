from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )


class FlexibleApiModel(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="allow",
    )


class RecommendationStrategy(StrEnum):
    MIN_RISK = "min_risk"
    MIN_COST = "min_cost"
    FASTEST = "fastest"
    BALANCED = "balanced"
    CUSTOM = "custom"


class TransportMode(StrEnum):
    ROAD = "road"
    RAIL = "rail"
    SEA = "sea"
    AIR = "air"


class CargoRequest(ApiModel):
    type: str = Field(min_length=1, description="货物类型，例如 finished_vehicle")
    vehicle_type: str | None = Field(default=None, description="车型，例如 electric_vehicle")
    quantity: int = Field(default=1, ge=1, le=100_000, description="整车或货物计费单位数量")
    gross_weight_kg: float | None = Field(default=None, gt=0)
    vehicle_length_m: float | None = Field(default=None, gt=0)
    vehicle_width_m: float | None = Field(default=None, gt=0)
    vehicle_height_m: float | None = Field(default=None, gt=0)
    container_type: str | None = None
    shipment_method: str | None = Field(default=None, description="例如 roro 或 container")

    @model_validator(mode="after")
    def validate_cargo_details(self) -> CargoRequest:
        dimensions = (self.vehicle_length_m, self.vehicle_width_m, self.vehicle_height_m)
        if any(value is not None for value in dimensions) and not all(value is not None for value in dimensions):
            raise ValueError("车辆尺寸必须同时提供 vehicleLengthM、vehicleWidthM、vehicleHeightM")
        if self.shipment_method is not None:
            self.shipment_method = self.shipment_method.strip().casefold()
            if self.shipment_method not in {"roro", "container"}:
                raise ValueError("shipmentMethod 只支持 roro 或 container")
        if self.container_type is not None:
            self.container_type = self.container_type.strip().casefold()
            if self.shipment_method != "container":
                raise ValueError("提供 containerType 时 shipmentMethod 必须为 container")
        return self


class RecommendationWeights(ApiModel):
    risk: float = Field(ge=0, le=1)
    cost: float = Field(ge=0, le=1)
    duration: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_sum(self) -> RecommendationWeights:
        total = self.risk + self.cost + self.duration
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"risk、cost、duration 权重之和必须等于 1，当前为 {total:.6f}")
        return self


class RecommendationConstraints(ApiModel):
    max_risk_score: float | None = Field(default=None, ge=0, le=100)
    max_cost_usd: float | None = Field(default=None, gt=0)
    max_duration_days: float | None = Field(default=None, gt=0)
    allowed_modes: list[TransportMode] = Field(default_factory=lambda: list(TransportMode))
    avoided_zone_ids: list[str] = Field(default_factory=list)
    min_data_completeness: float | None = Field(default=None, ge=0, le=1)
    require_known_risk: bool = False
    max_hops: int = Field(default=12, ge=1, le=20)

    @model_validator(mode="after")
    def validate_allowed_modes(self) -> RecommendationConstraints:
        if not self.allowed_modes:
            raise ValueError("allowedModes 不能为空")
        self.allowed_modes = list(dict.fromkeys(self.allowed_modes))
        self.avoided_zone_ids = list(dict.fromkeys(item.strip() for item in self.avoided_zone_ids if item.strip()))
        return self


class RecommendationRequest(ApiModel):
    supplier_id: str = Field(min_length=1, description="供应商 ID 或名称")
    origin: str = Field(min_length=1, description="起点 ID、节点名称或城市")
    destination: str = Field(min_length=1, description="终点 ID、节点名称或城市")
    cargo: CargoRequest
    strategy: RecommendationStrategy = RecommendationStrategy.BALANCED
    weights: RecommendationWeights | None = None
    constraints: RecommendationConstraints = Field(default_factory=RecommendationConstraints)
    limit: int = Field(default=5, ge=1, le=10)
    auto_reroute: bool = Field(default=True, description="自动避开仍在有效期内的高新闻风险路段")

    @model_validator(mode="after")
    def validate_request(self) -> RecommendationRequest:
        if self.origin.strip().casefold() == self.destination.strip().casefold():
            raise ValueError("origin 和 destination 不能相同")
        if self.strategy == RecommendationStrategy.CUSTOM and self.weights is None:
            raise ValueError("strategy=custom 时必须提供 weights")
        return self


class ScoreBounds(ApiModel):
    best: float
    worst: float


class NormalizationMetadata(ApiModel):
    method: str
    candidate_independent: bool = True
    risk_score: ScoreBounds
    cost_per_vehicle_usd: ScoreBounds
    duration_days: ScoreBounds


class CostEstimateResponse(ApiModel):
    currency: str = "USD"
    min: float | None = None
    most_likely: float | None = None
    max: float | None = None
    data_status: str
    provider: str | None = None
    confidence: float = Field(ge=0, le=1)
    formula: str
    cost_components: dict[str, float | None] = Field(default_factory=dict)
    missing_components: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    input_snapshot: dict[str, Any] = Field(default_factory=dict)


class DurationEstimateResponse(ApiModel):
    movement_duration_days: float | None = None
    waiting_duration_days: float | None = None
    customs_duration_days: float | None = None
    transfer_duration_days: float | None = None
    expected_delay_days: float | None = None
    total_duration_days: float | None = None
    duration_p50_days: float | None = None
    duration_p90_days: float | None = None
    data_status: str
    confidence: float = Field(ge=0, le=1)
    assumptions: list[str] = Field(default_factory=list)


class LocationResponse(FlexibleApiModel):
    id: str
    name: str = Field(min_length=1)
    city: str | None = None
    country: str | None = None
    country_code: str | None = None
    country_name_zh: str | None = None
    lat: float | None = None
    lng: float | None = None
    coordinate_source: str = "unavailable"
    coordinate_status: str = "unavailable"
    coordinate_confidence: float = 0.0


class RouteLegResponse(FlexibleApiModel):
    id: str
    from_: LocationResponse = Field(alias="from")
    to: LocationResponse
    mode: TransportMode
    cost: float | None = None
    duration_days: float | None = None
    distance_km: float | None = None
    risk_score: float | None = Field(default=None, ge=0, le=100)
    risk_status: str = "unavailable"


class RiskFactorResponse(FlexibleApiModel):
    key: str
    label: str
    score: float | None = Field(default=None, ge=0, le=100)
    status: str
    provider: str | None = None
    providers: list[str] = Field(default_factory=list)
    observed_at: str | None = None
    expires_at: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    affected_leg_ids: list[str] = Field(default_factory=list)
    evidence: list[Any] = Field(default_factory=list)
    detail: str | None = None


class RouteSubScores(ApiModel):
    risk: float | None = Field(default=None, ge=0, le=100)
    cost: float | None = Field(default=None, ge=0, le=100)
    duration: float | None = Field(default=None, ge=0, le=100)


class ScoreBreakdown(ApiModel):
    weights: RecommendationWeights
    sub_scores: RouteSubScores
    weighted_contributions: dict[str, float]
    base_score: float = Field(ge=0, le=100)
    uncertainty_penalty: float = Field(ge=0, le=100)
    final_score: float = Field(ge=0, le=100)
    data_completeness: float = Field(ge=0, le=1)


class RouteComparison(ApiModel):
    compared_with_route_id: str
    risk_score_delta: float | None = None
    cost_usd_delta: float | None = None
    duration_days_delta: float | None = None


class RecommendedRoute(FlexibleApiModel):
    id: str
    rank: int = Field(ge=1)
    name: str
    risk_score: float | None = Field(default=None, ge=0, le=100)
    risk_status: str
    risk_data_completeness: float = Field(ge=0, le=1)
    cost: float | None = None
    duration_days: float | None = None
    distance_km: float | None = None
    tags: list[str] = Field(default_factory=list)
    risk_factors: list[RiskFactorResponse] = Field(default_factory=list)
    legs: list[RouteLegResponse]
    score_breakdown: ScoreBreakdown
    final_score: float = Field(ge=0, le=100)
    uncertainty_penalty: float = Field(ge=0, le=100)
    data_completeness: float = Field(ge=0, le=1)
    cost_estimate: CostEstimateResponse
    duration_estimate: DurationEstimateResponse
    why_recommended: list[str] = Field(default_factory=list)
    comparison_to_next: RouteComparison | None = None
    avoided_risk_zones: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    estimated_fields: list[str] = Field(default_factory=list)


class DynamicRoutingResponse(ApiModel):
    rerouted: bool = False
    avoided_zones: list[str] = Field(default_factory=list)
    fallback_used: bool = False


class RejectedCandidate(ApiModel):
    route_id: str
    reasons: list[str]


class RecommendationResponse(ApiModel):
    snapshot_id: str
    scoring_version: str
    generated_at: datetime
    query: dict[str, Any]
    resolved_weights: RecommendationWeights
    normalization: NormalizationMetadata
    dynamic_routing: DynamicRoutingResponse
    candidate_count: int
    eligible_count: int
    count: int
    rejected_candidates: list[RejectedCandidate] = Field(default_factory=list)
    routes: list[RecommendedRoute]
