import json

import app.main as main


def route_snapshot():
    return {
        "snapshot_id": "recommendation-1",
        "created_at": "2026-07-31T00:00:00Z",
        "response_json": json.dumps(
            {
                "routes": [
                    {
                        "id": "route-1",
                        "name": "Shanghai to Rotterdam",
                        "riskScore": 58,
                        "riskStatus": "available",
                        "riskFactors": [
                            {
                                "key": "war",
                                "score": 72,
                                "status": "available",
                                "provider": "GDELT",
                                "providers": ["GDELT"],
                                "evidence": ["cluster-1"],
                                "affectedLegIds": ["SEG-SEA-1"],
                            }
                        ],
                        "legs": [
                            {
                                "id": "SEG-SEA-1",
                                "from": {"id": "PORT-CNSHG", "name": "上海港"},
                                "to": {"id": "PORT-NLRTM", "name": "鹿特丹港"},
                                "mode": "sea",
                                "riskScore": 58,
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
    }


def test_openapi_exposes_route_risk_news_endpoint():
    assert "/api/routes/{route_id}/risk-news" in main.app.openapi()["paths"]


def test_route_risk_news_returns_clickable_evidence_and_affected_legs(monkeypatch):
    queries = []

    def fake_query(query, parameters=None):
        queries.append((query, parameters))
        if "RecommendationSnapshot" in query:
            return [route_snapshot()]
        return [
            {
                "legId": "SEG-SEA-1",
                "segmentNewsRiskScore": 0.72,
                "segmentNewsRiskStatus": "available",
                "segmentNewsRiskUpdatedAt": "2026-07-31T00:00:00Z",
                "segmentNewsRiskExpiresAt": "2026-07-31T06:00:00Z",
                "clusterId": "cluster-1",
                "category": "conflict",
                "clusterTitle": "Shipping disruption near canal",
                "clusterSeverity": 0.8,
                "clusterEffectiveSeverity": 0.72,
                "clusterArticleCount": 3,
                "clusterSourceCount": 2,
                "clusterDomains": ["example.com", "example.org"],
                "clusterFirstSeen": "2026-07-30T22:00:00Z",
                "clusterLastSeen": "2026-07-31T00:00:00Z",
                "clusterExpiresAt": "2026-07-31T06:00:00Z",
                "clusterSourceCredibilityStatus": "multi_source",
                "active": True,
                "zoneId": "suez-canal",
                "zoneName": "苏伊士运河",
                "zoneRiskScore": 0.72,
                "zoneRiskLevel": "HIGH",
                "eventId": "article-1",
                "title": "Shipping disruption near canal",
                "url": "https://example.com/news/1",
                "canonicalUrl": "https://example.com/news/1",
                "domain": "example.com",
                "seenAt": "2026-07-31T00:00:00Z",
                "severity": 0.8,
                "eventCategory": "conflict",
                "matchedTerms": ["shipping disruption"],
                "sourceCredibilityStatus": "accepted",
            }
        ]

    monkeypatch.setattr(main, "safe_query", fake_query)
    result = main.recommended_route_risk_news(
        "route-1",
        active_only=True,
        category=None,
        limit=50,
    )

    assert result["routeId"] == "route-1"
    assert result["riskScoreEvidence"]["routeRiskScore"] == 58
    assert result["riskScoreEvidence"]["newsFactorScore"] == 72
    assert result["riskScoreEvidence"]["newsFactors"][0]["key"] == "war"
    assert result["riskScoreEvidence"]["articleLevelAllocation"] == "not_available"
    assert result["riskScoreEvidence"]["scoreBasis"] == "recommendation_snapshot"
    assert result["affectedLegs"][0]["newsRiskScore"] == 72.0
    assert result["events"][0]["url"] == "https://example.com/news/1"
    assert result["events"][0]["usedByRiskScore"] is True
    assert result["events"][0]["affectedLegIds"] == ["SEG-SEA-1"]
    assert result["zones"][0]["id"] == "suez-canal"
    assert "EXPOSED_TO_NEWS_CLUSTER" in queries[1][0]
    assert queries[1][1]["leg_ids"] == ["SEG-SEA-1"]
