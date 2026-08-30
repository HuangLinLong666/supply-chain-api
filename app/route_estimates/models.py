from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from app.recommendation.models import ApiModel


CONTRACT_VERSION = "route-estimates-v1"


class EstimateStatus(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    EXPIRED = "expired"


class ShipmentMethod(StrEnum):
    CONTAINER = "container"
    LCL = "lcl"
    RORO = "roro"


class ContainerType(StrEnum):
    TWENTY_FT = "20ft"
    FORTY_FT = "40ft"
    FORTY_HC = "40hc"


class RouteEstimateCargo(ApiModel):
    quantity: int = Field(ge=1, le=100, description="Freightos load-unit count")
    shipment_method: ShipmentMethod
    container_type: ContainerType | None = None
    gross_weight_kg: float | None = Field(default=None, gt=0, le=100_000)
    volume_m3: float | None = Field(default=None, gt=0, le=1_000)
    length_cm: float | None = Field(default=None, gt=0, le=3_000)
    width_cm: float | None = Field(default=None, gt=0, le=1_000)
    height_cm: float | None = Field(default=None, gt=0, le=1_000)

    @model_validator(mode="after")
    def validate_load(self) -> RouteEstimateCargo:
        dimensions = (self.length_cm, self.width_cm, self.height_cm)
        if any(value is not None for value in dimensions) and not all(value is not None for value in dimensions):
            raise ValueError("lengthCm、widthCm、heightCm 必须同时提供")
        if self.shipment_method == ShipmentMethod.CONTAINER and self.container_type is None:
            raise ValueError("shipmentMethod=container 时必须提供 containerType")
        if self.shipment_method != ShipmentMethod.CONTAINER and self.container_type is not None:
            raise ValueError("只有 shipmentMethod=container 可以提供 containerType")
        if self.shipment_method == ShipmentMethod.LCL:
            if self.gross_weight_kg is None:
                raise ValueError("shipmentMethod=lcl 时必须提供 grossWeightKg")
            if self.volume_m3 is None and not all(value is not None for value in dimensions):
                raise ValueError("shipmentMethod=lcl 时必须提供 volumeM3 或完整尺寸")
        return self


class RouteEstimateRequest(ApiModel):
    contract_version: Literal[CONTRACT_VERSION]
    origin_port_id: str = Field(pattern=r"^PORT-[A-Z]{2}[A-Z0-9]{3}$", max_length=10)
    destination_port_id: str = Field(pattern=r"^PORT-[A-Z]{2}[A-Z0-9]{3}$", max_length=10)
    cargo: RouteEstimateCargo

    @model_validator(mode="after")
    def validate_route(self) -> RouteEstimateRequest:
        if self.origin_port_id == self.destination_port_id:
            raise ValueError("originPortId 和 destinationPortId 不能相同")
        return self


class CostRange(ApiModel):
    min: float | None = None
    max: float | None = None


class TransitTimeRange(ApiModel):
    min_days: float | None = None
    max_days: float | None = None


class RouteEstimateResponse(ApiModel):
    contract_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION
    status: EstimateStatus
    provider: str = "Freightos"
    provider_product: str = "Public Marketplace Shipping Estimates API (Beta)"
    retrieved_at: str
    cache_expires_at: str | None = None
    provider_valid_until: str | None = None
    currency: str | None = None
    cost_range: CostRange
    transit_time_range: TransitTimeRange
    ranking_cost_usd: float | None = None
    ranking_duration_days: float | None = None
    ranking_policy: str = "conservative-upper-bound-v1"
    attribution_label: str = "Shipping estimate powered by Freightos"
    attribution_url: str = "https://ship.freightos.com"
    request_fingerprint: str
    response_sha256: str | None = None
    missing_data: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    cache_status: Literal["hit", "miss", "expired"] = "miss"

