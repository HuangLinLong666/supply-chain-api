from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main as main
from app.recommendation.engine import RecommendationEngine
from app.recommendation.models import RecommendationRequest, RecommendationResponse
from database.neo4j_client import close_driver, get_settings


VALIDATION_VERSION = "stage9-data-validation-v1"
FORBIDDEN_CYPHER = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|LOAD\s+CSV|FOREACH)\b",
    re.IGNORECASE,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def read_query(query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if FORBIDDEN_CYPHER.search(query):
        raise ValueError("阶段 9 数据验证只允许执行读查询")
    return main.safe_query(query, parameters)


def scalar_row(query: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = read_query(query, parameters)
    return rows[0] if rows else {}


def check(code: str, status: str, message: str, **metrics: Any) -> dict[str, Any]:
    return {"code": code, "status": status, "message": message, "metrics": metrics}


def recommendation_quality(result: dict[str, Any]) -> dict[str, int]:
    counters = Counter()
    for route in result.get("routes") or []:
        for leg in route.get("legs") or []:
            for endpoint in (leg.get("from") or {}, leg.get("to") or {}):
                if not str(endpoint.get("name") or "").strip():
                    counters["nodes_without_name"] += 1
                if not str(endpoint.get("coordinateStatus") or "").strip():
                    counters["nodes_without_coordinate_status"] += 1
            if str(leg.get("mode") or "") not in {"road", "rail", "sea", "air"}:
                counters["noncanonical_legs"] += 1
            if leg.get("feasibilityStatus") == "invalid_cross_ocean":
                counters["invalid_cross_ocean_legs"] += 1
            if leg.get("aisCongestionScore") is not None and not leg.get("aisCongestionEvidence"):
                counters["ais_scores_without_evidence"] += 1
        for factor in route.get("riskFactors") or []:
            if not factor.get("status"):
                counters["risk_factors_without_status"] += 1
            if factor.get("status") in {"available", "partial"} and not factor.get("provider"):
                counters["available_risk_factors_without_provider"] += 1
    return dict(counters)


def build_request(supplier_id: str, origin: str, destination: str) -> RecommendationRequest:
    return RecommendationRequest.model_validate(
        {
            "supplierId": supplier_id,
            "origin": origin,
            "destination": destination,
            "cargo": {
                "type": "finished_vehicle",
                "vehicleType": "electric_vehicle",
                "quantity": 1,
            },
            "strategy": "balanced",
            "constraints": {"allowedModes": ["road", "rail", "sea", "air"]},
            "limit": 5,
            "autoReroute": True,
        }
    )


def collect_validation(supplier_id: str, origin: str, destination: str) -> dict[str, Any]:
    generated_at = utc_now()
    settings = get_settings()
    checks: list[dict[str, Any]] = []
    database_counts = scalar_row(
        "MATCH (n) WITH count(n) AS nodes MATCH ()-[relationship]->() "
        "RETURN nodes,count(relationship) AS relationships"
    )
    duplicate_segments = scalar_row(
        "MATCH (segment:RouteSegment) "
        "WITH coalesce(segment.segment_id,segment.leg_id) AS identifier,count(*) AS occurrences "
        "WHERE identifier IS NOT NULL AND occurrences>1 RETURN count(*) AS duplicate_ids"
    )
    duplicate_count = int(duplicate_segments.get("duplicate_ids") or 0)
    checks.append(
        check(
            "UNIQUE_ROUTE_SEGMENT_IDS",
            "pass" if duplicate_count == 0 else "fail",
            "RouteSegment 业务标识没有重复" if duplicate_count == 0 else "发现重复 RouteSegment 业务标识",
            duplicate_ids=duplicate_count,
        )
    )

    risk_integrity = scalar_row(
        "MATCH (segment:RouteSegment) WHERE segment.provider_risk_score IS NOT NULL "
        "RETURN count(segment) AS scored_segments,"
        "count(CASE WHEN size(coalesce(segment.provider_risk_providers,[]))=0 THEN 1 END) AS missing_provider"
    )
    providerless = int(risk_integrity.get("missing_provider") or 0)
    checks.append(
        check(
            "PROVIDER_BACKED_RISK",
            "pass" if providerless == 0 else "fail",
            "数据库风险分均带 Provider" if providerless == 0 else "存在没有 Provider 的数据库风险分",
            **risk_integrity,
        )
    )

    locations = scalar_row(
        "MATCH (location:TransportLocation) "
        "RETURN count(location) AS total,"
        "count(CASE WHEN coalesce(location.name_zh,location.name_en,location.name,location.city,location.location_id) IS NOT NULL THEN 1 END) AS named,"
        "count(CASE WHEN location.coordinate_status IS NOT NULL THEN 1 END) AS with_coordinate_status,"
        "count(CASE WHEN location.latitude IS NOT NULL AND location.longitude IS NOT NULL THEN 1 END) AS with_coordinates"
    )
    location_total = int(locations.get("total") or 0)
    location_complete = (
        int(locations.get("named") or 0) == location_total
        and int(locations.get("with_coordinate_status") or 0) == location_total
    )
    checks.append(
        check(
            "LOCATION_METADATA",
            "pass" if location_complete else "warn",
            "TransportLocation 均有名称和坐标状态" if location_complete else "部分数据库地点仍需补齐元数据，API 会明确返回 unavailable",
            **locations,
        )
    )

    suppliers = scalar_row(
        "MATCH (supplier:Supplier) RETURN count(supplier) AS total,"
        "count(CASE WHEN EXISTS {(supplier)-[:SHIPS_FROM]->()} THEN 1 END) AS with_origins"
    )
    suppliers_without_origins = int(suppliers.get("total") or 0) - int(suppliers.get("with_origins") or 0)
    checks.append(
        check(
            "SUPPLIER_ORIGIN_MAPPING",
            "pass" if suppliers_without_origins == 0 else "warn",
            "所有供应商都有 SHIPS_FROM" if suppliers_without_origins == 0 else "部分供应商暂不可推荐，接口会拒绝猜测起点",
            **suppliers,
            without_origins=suppliers_without_origins,
        )
    )

    realtime = scalar_row(
        "MATCH (segment:RouteSegment) "
        "RETURN count(CASE WHEN properties(segment)['news_risk_expires_at'] IS NOT NULL "
        "AND datetime(toString(properties(segment)['news_risk_expires_at']))>datetime() THEN 1 END) AS active_gdelt_segments,"
        "count(CASE WHEN properties(segment)['route_weather_expires_at'] IS NOT NULL "
        "AND datetime(toString(properties(segment)['route_weather_expires_at']))>datetime() THEN 1 END) AS active_weather_segments,"
        "count(CASE WHEN properties(segment)['ais_congestion_expires_at'] IS NOT NULL "
        "AND datetime(toString(properties(segment)['ais_congestion_expires_at']))>datetime() THEN 1 END) AS active_ais_segments"
    )
    for field, code, provider in (
        ("active_gdelt_segments", "ACTIVE_GDELT", "GDELT"),
        ("active_weather_segments", "ACTIVE_WEATHER", "Open-Meteo"),
        ("active_ais_segments", "ACTIVE_AIS", "AISStream.io"),
    ):
        active_count = int(realtime.get(field) or 0)
        checks.append(
            check(
                code,
                "pass" if active_count else "warn",
                f"{provider} 当前有有效路线数据" if active_count else f"{provider} 当前没有未过期路线数据，不会伪造风险",
                active_segments=active_count,
            )
        )

    ais = scalar_row(
        "MATCH (snapshot:PortTrafficSnapshot) "
        "RETURN count(snapshot) AS snapshots,"
        "count(CASE WHEN snapshot.provider IS NULL OR snapshot.congestion_score IS NULL "
        "OR snapshot.observed_at IS NULL OR snapshot.expires_at IS NULL THEN 1 END) AS invalid_snapshots"
    )
    invalid_ais = int(ais.get("invalid_snapshots") or 0)
    checks.append(
        check(
            "AIS_SNAPSHOT_INTEGRITY",
            "pass" if invalid_ais == 0 else "fail",
            "AIS 快照为空或均具备 Provider、分数和时效字段" if invalid_ais == 0 else "发现字段不完整的 AIS 拥堵快照",
            **ais,
        )
    )

    segments = main.route_graph_segments()
    supplier = main.recommendation_supplier(supplier_id)
    request = build_request(supplier_id, origin, destination)
    result: dict[str, Any] = {
        "networkPathFound": False,
        "routes": [],
        "dynamicRouting": {"rerouted": False, "avoidedZones": [], "fallbackUsed": False},
    }
    recommendation_error: str | None = None
    if supplier is None:
        recommendation_error = f"供应商 {supplier_id} 不存在"
    else:
        matched_origins = main.matching_node_ids(segments, origin)
        destination_ids = main.matching_node_ids(segments, destination)
        origin_ids = main.supplier_origin_node_ids(segments, matched_origins, supplier)
        if not origin_ids:
            recommendation_error = f"供应商 {supplier_id} 与起点 {origin} 没有 SHIPS_FROM 映射"
        elif not destination_ids:
            recommendation_error = f"终点 {destination} 不在路线图中"
        else:
            result = RecommendationEngine().recommend(
                segments,
                origin_ids,
                destination_ids,
                supplier,
                request,
            )
            if not result.get("networkPathFound") or not result.get("routes"):
                recommendation_error = "真实路线图没有返回可用候选"
            else:
                engine = RecommendationEngine()
                try:
                    validated_response = RecommendationResponse.model_validate(
                        {
                            "snapshotId": "stage9-read-only-validation",
                            "scoringVersion": engine.scoring_version,
                            "generatedAt": generated_at,
                            "query": {
                                "supplierId": supplier_id,
                                "origin": origin,
                                "destination": destination,
                                "readOnly": True,
                            },
                            "resolvedWeights": engine.resolved_weights(request).model_dump(),
                            "normalization": engine.normalization_metadata(),
                            "dynamicRouting": result["dynamicRouting"],
                            "candidateCount": result["candidateCount"],
                            "eligibleCount": result["eligibleCount"],
                            "count": len(result["routes"]),
                            "rejectedCandidates": result["rejectedCandidates"],
                            "routes": result["routes"],
                        }
                    )
                except Exception as exc:
                    recommendation_error = f"RecommendationResponse 模型校验失败: {type(exc).__name__}: {exc}"
                else:
                    result = validated_response.model_dump(mode="json", by_alias=True)
    quality = recommendation_quality(result)
    quality_failures = sum(quality.values())
    checks.append(
        check(
            "READ_ONLY_RECOMMENDATION_SMOKE",
            "fail" if recommendation_error or quality_failures else "pass",
            recommendation_error or "只读推荐成功，节点、模式、风险来源和 AIS 证据契约有效",
            supplier_id=supplier_id,
            origin=origin,
            destination=destination,
            route_graph_segments=len(segments),
            returned_routes=len(result.get("routes") or []),
            quality=quality,
            dynamic_routing=result.get("dynamicRouting"),
        )
    )

    statuses = Counter(item["status"] for item in checks)
    return {
        "metadata": {
            "validation_version": VALIDATION_VERSION,
            "scoring_version": RecommendationEngine().scoring_version,
            "generated_at_utc": generated_at.isoformat(),
            "database": settings.database or "default",
            "read_only": True,
            "credentials_redacted": True,
        },
        "summary": {
            "status": "fail" if statuses["fail"] else "pass_with_warnings" if statuses["warn"] else "pass",
            "checks": len(checks),
            "passed": statuses["pass"],
            "warnings": statuses["warn"],
            "failed": statuses["fail"],
            "database_counts": database_counts,
        },
        "checks": checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="阶段 9 AuraDB 与推荐结果只读验证")
    parser.add_argument("--supplier-id", default="SUP-CATL")
    parser.add_argument("--origin", default="Shanghai")
    parser.add_argument("--destination", default="Hamburg")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    parser.add_argument("--strict-warnings", action="store_true", help="将数据时效警告也视为失败")
    return parser.parse_args()


def main_cli() -> int:
    args = parse_args()
    try:
        validation = collect_validation(args.supplier_id, args.origin, args.destination)
    finally:
        close_driver()
    timestamp = datetime.fromisoformat(validation["metadata"]["generated_at_utc"]).strftime("%Y%m%dT%H%M%SZ")
    output_path = args.output_dir / f"stage9_data_validation_{timestamp}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": validation["summary"]["status"],
                "passed": validation["summary"]["passed"],
                "warnings": validation["summary"]["warnings"],
                "failed": validation["summary"]["failed"],
                "readOnly": True,
                "output": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if validation["summary"]["failed"]:
        return 1
    if args.strict_warnings and validation["summary"]["warnings"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
