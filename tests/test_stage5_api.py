import app.main as main


def test_openapi_exposes_stage5_frontend_endpoints():
    paths = main.app.openapi()["paths"]
    assert "/api/risk/news/clusters" in paths
    assert "/api/routes/weather-risks" in paths
    assert "/api/routes/weather-risks/{segment_id}" in paths


def test_news_zone_endpoint_decodes_audit_counts(monkeypatch):
    monkeypatch.setattr(
        main,
        "safe_query",
        lambda query, parameters=None: [
            {
                "id": "red-sea",
                "categoryCounts": '{"conflict":2}',
                "rejectedCounts": '{"exact_duplicate":1}',
            }
        ],
    )
    result = main.news_risk_zones()
    assert result["zones"][0]["categoryCounts"] == {"conflict": 2}
    assert result["zones"][0]["rejectedCounts"] == {"exact_duplicate": 1}


def test_route_graph_query_rejects_expired_provider_risk(monkeypatch):
    queries = []
    monkeypatch.setattr(main, "_route_graph_cache", None)
    monkeypatch.setattr(
        main,
        "safe_query",
        lambda query, parameters=None: queries.append(query) or [],
    )
    assert main.route_graph_segments() == []
    assert "provider_risk_expires_at" in queries[0]
    assert "datetime(toString(segment.provider_risk_expires_at)) > datetime()" in queries[0]


def test_route_weather_detail_decodes_samples(monkeypatch):
    monkeypatch.setattr(
        main,
        "safe_query",
        lambda query, parameters=None: [
            {
                "segmentId": "SEG-1",
                "segmentWeather": {"route_weather_risk": 42},
                "latestSnapshot": {
                    "samples_json": '[{"score":42}]',
                    "risk_factors_json": '[{"factor":"wave"}]',
                },
            }
        ],
    )
    result = main.route_weather_risk_detail("SEG-1")
    assert result["latestSnapshot"]["samples"] == [{"score": 42}]
    assert result["latestSnapshot"]["risk_factors"] == [{"factor": "wave"}]
