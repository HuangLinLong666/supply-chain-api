from __future__ import annotations

from dataclasses import dataclass


SCHEMA_VERSION = "unified-transport-v1"
MIGRATION_CONFIRMATION = "APPLY_UNIFIED_SCHEMA_V1"


@dataclass(frozen=True)
class SchemaRule:
    name: str
    label: str
    property: str

    @property
    def statement(self) -> str:
        return (
            f"CREATE CONSTRAINT {self.name} IF NOT EXISTS "
            f"FOR (node:{self.label}) REQUIRE node.{self.property} IS UNIQUE"
        )


@dataclass(frozen=True)
class IndexRule:
    name: str
    label: str
    property: str

    @property
    def statement(self) -> str:
        return (
            f"CREATE INDEX {self.name} IF NOT EXISTS "
            f"FOR (node:{self.label}) ON (node.{self.property})"
        )


IDENTITY_CONSTRAINTS = (
    SchemaRule("unified_supplier_id_unique", "Supplier", "supplier_id"),
    SchemaRule("unified_factory_id_unique", "Factory", "factory_id"),
    SchemaRule("unified_warehouse_id_unique", "Warehouse", "warehouse_id"),
    SchemaRule("unified_transport_location_id_unique", "TransportLocation", "location_id"),
    SchemaRule("unified_route_segment_id_unique", "RouteSegment", "segment_id"),
    SchemaRule("unified_route_id_unique", "Route", "route_id"),
    SchemaRule("unified_geo_zone_id_unique", "GeoZone", "zone_id"),
    SchemaRule("unified_risk_observation_id_unique", "RiskObservation", "observation_id"),
    SchemaRule("unified_cost_observation_id_unique", "CostObservation", "observation_id"),
    SchemaRule("unified_delay_observation_id_unique", "DelayObservation", "observation_id"),
    SchemaRule("unified_evidence_id_unique", "Evidence", "evidence_id"),
    SchemaRule("unified_vessel_mmsi_unique", "Vessel", "mmsi"),
    SchemaRule("unified_port_traffic_snapshot_id_unique", "PortTrafficSnapshot", "snapshot_id"),
    SchemaRule("unified_recommendation_snapshot_id_unique", "RecommendationSnapshot", "snapshot_id"),
)


QUERY_INDEXES = (
    IndexRule("unified_transport_location_kind", "TransportLocation", "location_kind"),
    IndexRule("unified_transport_location_country", "TransportLocation", "country_code"),
    IndexRule("unified_transport_location_status", "TransportLocation", "data_status"),
    IndexRule("unified_route_segment_mode", "RouteSegment", "canonical_mode"),
    IndexRule("unified_route_segment_status", "RouteSegment", "data_status"),
    IndexRule("unified_route_status", "Route", "data_status"),
    IndexRule("unified_geo_zone_type", "GeoZone", "zone_type"),
    IndexRule("unified_geo_zone_updated", "GeoZone", "updated_at"),
    IndexRule("unified_risk_observation_observed", "RiskObservation", "observed_at"),
    IndexRule("unified_risk_observation_expires", "RiskObservation", "expires_at"),
    IndexRule("unified_cost_observation_observed", "CostObservation", "observed_at"),
    IndexRule("unified_delay_observation_observed", "DelayObservation", "observed_at"),
    IndexRule("unified_evidence_collected", "Evidence", "collected_at"),
    IndexRule("unified_vessel_last_observed", "Vessel", "last_ais_observed_at"),
    IndexRule("unified_port_traffic_observed", "PortTrafficSnapshot", "observed_at"),
    IndexRule("unified_recommendation_created", "RecommendationSnapshot", "created_at"),
)


TARGET_LABELS = (
    "Supplier",
    "Factory",
    "Warehouse",
    "TransportLocation",
    "Port",
    "Airport",
    "RailTerminal",
    "RoadTerminal",
    "RouteSegment",
    "Route",
    "GeoZone",
    "RiskObservation",
    "CostObservation",
    "DelayObservation",
    "Evidence",
    "Vessel",
    "PortTrafficSnapshot",
    "RecommendationSnapshot",
)


LEGACY_SOURCE_LABELS = (
    "ArrivalWarehouse",
    "CostEstimate",
    "CountryRiskObservation",
    "DepartureWarehouse",
    "ExportWarehouse",
    "NewsRiskEvent",
    "NewsRiskZone",
    "OverseasWarehouse",
    "RiskSnapshot",
    "RouteCostObservation",
    "RouteDelayObservation",
    "RouteLeg",
    "SourceEvidence",
    "VehicleRoute",
    "WeatherRiskSnapshot",
)


ALL_MIGRATION_LABELS = tuple(dict.fromkeys((*TARGET_LABELS, *LEGACY_SOURCE_LABELS)))


def unified_schema_statements() -> list[str]:
    return [rule.statement for rule in (*IDENTITY_CONSTRAINTS, *QUERY_INDEXES)]


def data_status_for_source_type(
    source_type: str | None,
    *,
    review_status: str | None = None,
    is_inferred: bool = False,
) -> str:
    if (review_status or "").casefold() in {"approved", "verified", "reviewed"}:
        return "verified"
    normalized = (source_type or "").casefold()
    if normalized in {"official_registry", "official_schedule"}:
        return "verified"
    if normalized in {"paid_api", "open_api", "ais_observed", "flight_observed"}:
        return "observed"
    if normalized == "estimated_by_graph" or is_inferred:
        return "estimated"
    if normalized == "fabricated_for_testing":
        return "synthetic"
    return "unavailable"
