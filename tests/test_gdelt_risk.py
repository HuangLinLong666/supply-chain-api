from gdelt.exposure import inferred_exposure
from datetime import datetime, timezone

from gdelt.risk import article_id, article_severity, canonicalize_url, score_zone
from gdelt.repository import apply_segment_overlay, write_zone


def test_attack_news_creates_high_risk():
    now = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    articles = [
        {
            "url": f"https://source-{index}.example/news",
            "title": "Missile attack closes Red Sea shipping lane",
            "seendate": "20260726T120000Z",
            "domain": f"source-{index}.example",
        }
        for index in range(6)
    ]
    result = score_zone(articles, now=now)
    assert result["score"] >= 0.8
    assert result["level"] == "CRITICAL"
    assert result["cluster_count"] == 1


def test_duplicate_urls_are_deduplicated():
    article = {
        "url": "https://example.com/one?utm_source=test",
        "title": "Shipping disruption",
        "seendate": "20260726T115500Z",
    }
    duplicate = {**article, "url": "https://example.com/one?utm_campaign=again"}
    result = score_zone(
        [article, duplicate],
        now=datetime(2026, 7, 26, 12, tzinfo=timezone.utc),
    )
    assert len(result["articles"]) == 1
    assert result["rejected_counts"] == {"exact_duplicate": 1}
    assert article_id(article) == article_id(article)
    assert canonicalize_url(article["url"]) == "https://example.com/one"


def test_neutral_article_has_low_base_severity():
    score, terms = article_severity({"title": "Port publishes annual report"})
    assert score == 0.25
    assert terms == []


def test_asia_europe_sea_segment_is_red_sea_exposed():
    segment = {"mode": "sea", "from_country": "Singapore", "to_country": "Netherlands"}
    assert inferred_exposure("red-sea", segment)
    assert inferred_exposure("malacca-strait", segment)
    assert inferred_exposure("indian-ocean", segment)


def test_cape_route_is_not_red_sea_exposed():
    assert not inferred_exposure("red-sea", {"mode": "sea", "from_name": "Singapore Port", "to_name": "Cape Town", "from_country": "Singapore", "to_country": "South Africa"})


def test_air_route_is_not_red_sea_exposed():
    assert not inferred_exposure("red-sea", {"mode": "air", "from_country": "China", "to_country": "Germany"})


def test_trans_pacific_route_is_exposed_to_pacific():
    assert inferred_exposure("pacific-ocean", {"mode": "sea", "from_country": "China", "to_country": "United States"})


def test_middle_east_affects_air_routes():
    assert inferred_exposure("middle-east", {"mode": "air", "from_country": "China", "to_country": "Germany"})


def test_segment_overlay_uses_provider_score_without_neutral_base(monkeypatch):
    writes = []
    monkeypatch.setattr("gdelt.repository.run_query", lambda query, parameters=None: writes.append((query, parameters)) or [])
    apply_segment_overlay(
        {"element_id": "segment-1", "mode": "sea"},
        {
            "red-sea": {
                "score": 0.8,
                "confidence": 0.7,
                "decision_factors": {
                    "war": {
                        "score": 0.8,
                        "confidence": 0.7,
                        "cluster_ids": ["cluster-war"],
                        "observed_at": "2026-07-26T12:00:00+00:00",
                    }
                },
            }
        },
        ["red-sea"],
        3,
    )
    properties = writes[0][1]["risk_properties"]
    assert properties["provider_risk_score"] == 0.8
    assert properties["provider_risk_data_completeness"] == 0.4
    assert properties["provider_risk_providers"] == ["GDELT"]


def test_unexposed_segment_overlay_keeps_risk_unavailable(monkeypatch):
    writes = []
    monkeypatch.setattr("gdelt.repository.run_query", lambda query, parameters=None: writes.append((query, parameters)) or [])
    apply_segment_overlay({"element_id": "segment-1", "mode": "sea"}, {}, [], 3)
    parameters = writes[0][1]
    assert parameters["news_risk"] is None
    assert parameters["risk_properties"]["provider_risk_score"] is None
    assert parameters["risk_properties"]["provider_risk_status"] == "unavailable"


def test_tariff_news_increases_severity():
    score, terms = article_severity({"title": "New tariffs imposed on shipping route"})
    assert score == 0.55
    assert "tariffs" in terms


def test_zone_scoring_separates_three_decision_factors():
    now = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    result = score_zone(
        [
            {"url": "https://one.example/war", "domain": "one.example", "title": "War closes shipping lane", "seendate": "20260726T120000Z"},
            {"url": "https://two.example/flood", "domain": "two.example", "title": "Flood closes rail corridor", "seendate": "20260726T120000Z"},
            {"url": "https://three.example/tariff", "domain": "three.example", "title": "New tariffs imposed", "seendate": "20260726T120000Z"},
        ],
        now=now,
    )

    assert set(result["decision_factors"]) == {"war", "natural_disaster", "trade_policy"}
    assert all(factor["cluster_ids"] for factor in result["decision_factors"].values())


def test_future_or_missing_timestamp_is_not_scored_as_current_news():
    now = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    result = score_zone(
        [
            {"url": "https://example.com/future", "title": "War closes port", "seendate": "20260727T120000Z"},
            {"url": "https://example.com/missing", "title": "War closes port"},
        ],
        now=now,
    )
    assert result["score"] is None
    assert result["status"] == "unavailable"
    assert result["valid_article_count"] == 0
    assert result["rejected_counts"] == {"invalid_or_future_seen_at": 2}


def test_near_duplicate_titles_score_as_one_cluster():
    now = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)
    result = score_zone(
        [
            {
                "url": "https://one.example/a",
                "domain": "one.example",
                "title": "Missile attack closes the Red Sea shipping lane",
                "seendate": "20260726T110000Z",
            },
            {
                "url": "https://two.example/b",
                "domain": "two.example",
                "title": "Red Sea shipping lane closes after missile attack",
                "seendate": "20260726T111000Z",
            },
        ],
        now=now,
        similarity_threshold=0.7,
    )
    assert result["cluster_count"] == 1
    assert result["clusters"][0]["article_count"] == 2
    assert result["clusters"][0]["distinct_domain_count"] == 2


def test_zone_writer_persists_classification_and_cluster_audit_fields(monkeypatch):
    writes = []
    monkeypatch.setattr(
        "gdelt.repository.run_query",
        lambda query, parameters=None: writes.append((query, parameters)) or [],
    )
    now = datetime.now(timezone.utc)
    result = score_zone(
        [
            {
                "url": "https://example.com/event",
                "domain": "example.com",
                "title": "Port congestion disrupts shipping",
                "seendate": now.strftime("%Y%m%dT%H%M%SZ"),
            }
        ],
        now=now,
        cluster_namespace="port-shanghai",
    )
    write_zone(
        {"id": "port-shanghai", "name": "上海港", "type": "port", "query": "test"},
        result,
        "gdelt-event-cluster-v3",
        3,
    )
    assert writes[0][1]["status"] == "available"
    assert writes[0][1]["cluster_count"] == 1
    article_parameters = next(parameters for _, parameters in writes if parameters and "articles" in parameters)
    cluster_parameters = next(parameters for _, parameters in writes if parameters and "clusters" in parameters)
    assert article_parameters["articles"][0]["event_category"] == "port_disruption"
    assert cluster_parameters["clusters"][0]["cluster_id"].startswith("gdelt-cluster-port shanghai-")
