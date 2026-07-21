from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """返回带时区的 UTC 时间。"""
    return datetime.now(timezone.utc)


class SourceType(StrEnum):
    OFFICIAL_REGISTRY = "official_registry"
    OFFICIAL_SCHEDULE = "official_schedule"
    PAID_API = "paid_api"
    OPEN_API = "open_api"
    AIS_OBSERVED = "ais_observed"
    FLIGHT_OBSERVED = "flight_observed"
    ESTIMATED_BY_GRAPH = "estimated_by_graph"
    MANUAL_WEB_RESEARCH = "manual_web_research"
    USER_CREATED = "user_created"
    FABRICATED_FOR_TESTING = "fabricated_for_testing"


class LocationKind(StrEnum):
    PORT = "port"
    AIRPORT = "airport"
    FACTORY = "factory"
    RAIL_TERMINAL = "rail_terminal"
    ROAD_TERMINAL = "road_terminal"


class Provenance(BaseModel):
    source: str
    source_url: str | None = None
    source_type: SourceType
    collected_at: datetime = Field(default_factory=utc_now)
    confidence: float = Field(ge=0, le=1)
    is_inferred: bool = False
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_status: str = "pending"


class LocationRecord(Provenance):
    id: str
    kind: LocationKind
    name_zh: str | None = None
    name_en: str
    country_code: str = Field(min_length=2, max_length=2)
    unlocode: str | None = None
    iata: str | None = None
    icao: str | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    aliases: list[str] = []
    eligible_for_vehicle_export: bool = False
    eligible_for_vehicle_import: bool = False
    updated_at: datetime = Field(default_factory=utc_now)


class EvidenceRecord(BaseModel):
    evidence_id: str = Field(default_factory=lambda: f"evidence_{uuid4().hex}")
    source: str
    source_url: str | None = None
    source_type: SourceType
    collected_at: datetime = Field(default_factory=utc_now)
    raw_excerpt: str | None = None
    confidence: float = Field(ge=0, le=1)


class RouteLegRecord(Provenance):
    leg_id: str
    sequence: int = Field(ge=1)
    mode: str
    origin_id: str
    destination_id: str
    distance_km: float = Field(ge=0)
    duration_h: float = Field(ge=0)
    geometry: list[list[float]] = []
    carrier: str | None = None
    flight_number: str | None = None
    voyage_number: str | None = None
    vessel_name: str | None = None
    aircraft_type: str | None = None
    train_number: str | None = None
    historical_supported: bool = False
    evidence_refs: list[str] = []


class CostRange(BaseModel):
    currency: str = "USD"
    min: float = Field(ge=0)
    most_likely: float = Field(ge=0)
    max: float = Field(ge=0)
    formula_explanation: str
    input_snapshot: dict[str, Any]


class RiskResult(BaseModel):
    risk_score: float = Field(ge=0, le=100)
    risk_level: str
    risk_factors: list[str]
    evidence_refs: list[str] = []


class RouteRecord(Provenance):
    route_id: str
    route_type: str
    origin_id: str
    destination_id: str
    legs_count: int = Field(ge=1)
    estimated_distance_km: float = Field(ge=0)
    estimated_duration_h: float = Field(ge=0)
    route_status: str = "candidate"
    evidence_count: int = 0
    historical_supported: bool = False
    score: float = Field(default=0, ge=0, le=1)
    estimated_cost: CostRange | None = None
    risk: RiskResult | None = None
    why_recommended: list[str] = []
    needs_review: bool = True
    legs: list[RouteLegRecord]


class LocationIngestRequest(BaseModel):
    country_scope: list[str] = ["CN", "US", "DE", "BR", "MX", "AE"]
    include_ports: bool = True
    include_airports: bool = True
    include_rail_terminals: bool = True
    include_road_terminals: bool = True
    force_refresh: bool = False


class RouteGenerateRequest(BaseModel):
    origin: str
    destination: str
    origin_kind: str | None = None
    destination_kind: str | None = None
    mode_preferences: list[str] = []
    allow_multimodal: bool = True
    max_transfers: int = Field(default=3, ge=0, le=6)
    ranking_strategy: str = "hybrid"
    prefer_observed_routes: bool = True
    persist: bool = True


class ReviewRequest(BaseModel):
    review_status: str
    reviewed_by: str
    note: str | None = None


class AuditSourceRequest(BaseModel):
    entity_id: str
    entity_type: str
    source: str
    source_url: str | None = None
    source_type: SourceType
    confidence: float = Field(ge=0, le=1)
    note: str | None = None


class StrategyConfig(BaseModel):
    risk_weights: dict[str, float]
    ranking_weights: dict[str, float]
    high_risk_threshold: float = 60
    critical_risk_threshold: float = 80
    default_ranking_strategy: str = "hybrid"
