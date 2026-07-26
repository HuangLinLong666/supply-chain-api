from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.recommendation.models import RecommendationRequest, RecommendationResponse


QueryFunction = Callable[[str, dict[str, Any] | None], list[dict[str, Any]]]


CREATE_SNAPSHOT_QUERY = """
MERGE (snapshot:RecommendationSnapshot {snapshot_id:$snapshot_id})
SET snapshot.scoring_version=$scoring_version,
    snapshot.schema_version='unified-transport-v1',
    snapshot.strategy=$strategy,
    snapshot.supplier_id=$supplier_id,
    snapshot.origin=$origin,
    snapshot.destination=$destination,
    snapshot.input_snapshot_json=$input_snapshot_json,
    snapshot.weights_json=$weights_json,
    snapshot.constraints_json=$constraints_json,
    snapshot.response_json=$response_json,
    snapshot.route_ids=$route_ids,
    snapshot.candidate_count=$candidate_count,
    snapshot.eligible_count=$eligible_count,
    snapshot.data_status='computed',
    snapshot.created_at=datetime($created_at),
    snapshot.expires_at=datetime($created_at)+duration({days:$retention_days})
RETURN snapshot.snapshot_id AS snapshot_id
"""


LINK_SEGMENTS_QUERY = """
MATCH (snapshot:RecommendationSnapshot {snapshot_id:$snapshot_id})
UNWIND $inclusions AS inclusion
MATCH (segment:RouteSegment)
WHERE coalesce(segment.segment_id,segment.segmentId,elementId(segment))=inclusion.segment_id
MERGE (segment)-[relationship:INCLUDED_IN]->(snapshot)
SET relationship.route_ids=inclusion.route_ids,
    relationship.scoring_version=$scoring_version,
    relationship.created_at=datetime($created_at)
RETURN count(relationship) AS linked_segments
"""


GET_SNAPSHOT_QUERY = """
MATCH (snapshot:RecommendationSnapshot {snapshot_id:$snapshot_id})
RETURN snapshot.response_json AS response_json,
       snapshot.created_at AS created_at,
       snapshot.expires_at AS expires_at
"""


GET_ROUTE_QUERY = """
MATCH (snapshot:RecommendationSnapshot)
WHERE $route_id IN coalesce(snapshot.route_ids,[])
RETURN snapshot.snapshot_id AS snapshot_id,
       snapshot.response_json AS response_json,
       snapshot.created_at AS created_at
ORDER BY snapshot.created_at DESC
LIMIT 1
"""


def persist_recommendation_snapshot(
    query: QueryFunction,
    request: RecommendationRequest,
    response: RecommendationResponse,
    inclusions: list[dict[str, Any]],
    retention_days: int,
) -> None:
    request_data = request.model_dump(mode="json", by_alias=True)
    response_data = response.model_dump(mode="json", by_alias=True)
    parameters = {
        "snapshot_id": response.snapshot_id,
        "scoring_version": response.scoring_version,
        "strategy": request.strategy.value,
        "supplier_id": request.supplier_id,
        "origin": request.origin,
        "destination": request.destination,
        "input_snapshot_json": json.dumps(request_data, ensure_ascii=False, sort_keys=True),
        "weights_json": json.dumps(response_data["resolvedWeights"], ensure_ascii=False, sort_keys=True),
        "constraints_json": json.dumps(request_data["constraints"], ensure_ascii=False, sort_keys=True),
        "response_json": json.dumps(response_data, ensure_ascii=False, sort_keys=True),
        "route_ids": [route.id for route in response.routes],
        "candidate_count": response.candidate_count,
        "eligible_count": response.eligible_count,
        "created_at": response.generated_at.isoformat(),
        "retention_days": retention_days,
    }
    query(CREATE_SNAPSHOT_QUERY, parameters)
    if inclusions:
        query(
            LINK_SEGMENTS_QUERY,
            {
                "snapshot_id": response.snapshot_id,
                "inclusions": inclusions,
                "scoring_version": response.scoring_version,
                "created_at": response.generated_at.isoformat(),
            },
        )
