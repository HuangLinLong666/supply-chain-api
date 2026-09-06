from scripts.audit_route_mode_rerouting import audit


def test_audit_reports_mode_counts_and_real_hormuz_coverage_without_synthetic_writes():
    fixtures = iter([
        [{"zoneId": "hormuz-strait", "name": "霍尔木兹海峡", "labels": ["GeoZone"]}],
        [{"segmentId": "SEA-HORMUZ", "mode": "sea", "dataStatus": "observed", "source": "provider", "fromId": "A", "toId": "B"}],
        [{"mode": "road", "candidateSegmentCount": 12}, {"mode": "sea", "candidateSegmentCount": 8}],
        [{"segmentId": "ROAD-ALT", "mode": "road", "dataStatus": "observed", "fromId": "A", "toId": "B"}],
    ])
    queries = []

    def fake_query(statement, parameters=None):
        queries.append((statement, parameters))
        return next(fixtures)

    report = audit(fake_query)
    assert report["passed"] is True
    assert report["syntheticRoutesCreated"] == 0
    assert report["rerouteBlockers"] == []
    assert {row["mode"] for row in report["modeCandidateCounts"]} == {"road", "sea"}
    assert len(queries) == 4
