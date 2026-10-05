from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel

from app.recommendation.models import RecommendationRequest


RouteRecommendationErrorCode = Literal[
    "no_candidates",
    "origin_not_found",
    "destination_not_found",
    "supplier_not_found",
    "supplier_origin_unmapped",
    "supplier_origin_mismatch",
    "invalid_constraints",
]


class RouteRecommendationErrorDetail(BaseModel):
    code: RouteRecommendationErrorCode
    message: str


class RouteRecommendationErrorResponse(BaseModel):
    detail: RouteRecommendationErrorDetail


class InvalidRecommendationConstraints(Exception):
    """Raised only for deterministic cross-field business constraint conflicts."""


ERROR_CONTRACT: dict[RouteRecommendationErrorCode, tuple[int, str]] = {
    "no_candidates": (404, "No feasible route candidates were found"),
    "origin_not_found": (404, "Origin was not found"),
    "destination_not_found": (404, "Destination was not found"),
    "supplier_not_found": (404, "Supplier was not found"),
    "supplier_origin_unmapped": (422, "Supplier has no shipping origin mapping"),
    "supplier_origin_mismatch": (422, "Origin is not linked to the supplied supplier"),
    "invalid_constraints": (400, "Required transport modes cannot be satisfied within maxHops"),
}


ROUTE_RECOMMENDATION_ERROR_RESPONSES = {
    400: {"model": RouteRecommendationErrorResponse, "description": "Invalid business constraints"},
    404: {"model": RouteRecommendationErrorResponse, "description": "Route resource or candidates not found"},
    422: {
        "description": "Request validation or route recommendation business error",
        "content": {
            "application/json": {
                "schema": {
                    "oneOf": [
                        {"$ref": "#/components/schemas/HTTPValidationError"},
                        {"$ref": "#/components/schemas/RouteRecommendationErrorResponse"},
                    ]
                }
            }
        },
    },
}


def route_recommendation_error(code: RouteRecommendationErrorCode) -> HTTPException:
    status_code, message = ERROR_CONTRACT[code]
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def validate_recommendation_business_constraints(request: RecommendationRequest) -> None:
    required_mode_count = len(set(request.constraints.required_modes))
    if required_mode_count > request.constraints.max_hops:
        raise InvalidRecommendationConstraints
