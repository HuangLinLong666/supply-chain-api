from __future__ import annotations

from typing import Any, Callable

from app.route_estimates.config import RouteEstimateSettings
from app.route_estimates.locations import LocationResolutionError, QueryFunction, resolve_port_unlocode


DOWNSTREAM_STAGE_HEADER = "X-Route-Estimate-Stage"
DOWNSTREAM_OUTCOME_HEADER = "X-Route-Estimate-Outcome"
DEMO_PORT_IDS = ("PORT-CNSHG", "PORT-USLAX")

ALLOWED_DOWNSTREAM_STAGES = frozenset(
    {
        "request_validation",
        "authentication",
        "location_resolution",
        "service_initialization",
        "response_validation",
        "internal",
    }
)
ALLOWED_DOWNSTREAM_OUTCOMES = frozenset(
    {
        "request_invalid",
        "authentication_rejected",
        "authentication_configuration_unavailable",
        "route_estimate_settings_invalid",
        "location_identity_invalid",
        "location_registry_unavailable",
        "service_initialization_failed",
        "response_validation_failed",
        "unhandled_internal_error",
    }
)


def diagnostic_headers(stage: str, outcome: str) -> dict[str, str]:
    if stage not in ALLOWED_DOWNSTREAM_STAGES or outcome not in ALLOWED_DOWNSTREAM_OUTCOMES:
        raise ValueError("Unsupported route-estimate diagnostic classification")
    return {
        DOWNSTREAM_STAGE_HEADER: stage,
        DOWNSTREAM_OUTCOME_HEADER: outcome,
    }


def route_estimate_health_snapshot(
    query: QueryFunction,
    settings_factory: Callable[[], RouteEstimateSettings] = RouteEstimateSettings,
) -> dict[str, str]:
    result = {
        "status": "unavailable",
        "configurationStatus": "not_checked",
        "authenticationStatus": "not_checked",
        "locationRegistryStatus": "not_checked",
        "providerStatus": "not_called",
    }
    try:
        settings = settings_factory()
    except (TypeError, ValueError):
        result["configurationStatus"] = "invalid"
        return result

    result["configurationStatus"] = "valid"
    if not settings.service_token:
        result["authenticationStatus"] = "unconfigured"
        return result
    result["authenticationStatus"] = "configured"

    try:
        for location_id in DEMO_PORT_IDS:
            resolve_port_unlocode(location_id, query)
    except LocationResolutionError:
        result["locationRegistryStatus"] = "invalid_identity"
        return result
    except RuntimeError:
        result["locationRegistryStatus"] = "unavailable"
        return result
    except Exception:
        result["locationRegistryStatus"] = "unavailable"
        return result

    result["locationRegistryStatus"] = "ready"
    result["status"] = "ready"
    return result
