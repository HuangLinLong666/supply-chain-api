from datetime import datetime, timezone

from gdelt.service import update_news_risk


class FakeClient:
    def search(self, query):
        return [
            {
                "url": f"https://example.test/{query}",
                "domain": "example.test",
                "title": "Missile attack disrupts shipping",
                "seendate": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            }
        ]


class PartiallyFailingClient(FakeClient):
    def search(self, query):
        if query == "failed-query":
            raise RuntimeError("provider timeout")
        return super().search(query)


def test_update_can_target_one_zone_for_debugging(monkeypatch):
    monkeypatch.setattr(
        "gdelt.service.load_zone_config",
        lambda: {
            "scoring_version": "gdelt-event-cluster-v3",
            "zones": [
                {"id": "one", "name": "One", "type": "region", "query": "one"},
                {"id": "two", "name": "Two", "type": "region", "query": "two"},
            ],
        },
    )
    monkeypatch.setattr("gdelt.service.route_segments", lambda: [])
    result = update_news_risk(dry_run=True, client=FakeClient(), zone_ids=["two"])
    assert result["zonesRequested"] == 1
    assert result["zonesUpdated"] == 1
    assert set(result["zoneRisks"]) == {"two"}


def test_partial_zone_failure_does_not_skip_whole_route(monkeypatch):
    zones = [
        {"id": "available-zone", "name": "Available", "type": "region", "query": "ok"},
        {"id": "failed-zone", "name": "Failed", "type": "region", "query": "failed-query"},
    ]
    segment = {
        "element_id": "segment-1",
        "segment_id": "SEG-1",
        "mode": "sea",
        "spatial_exposures": [
            {"zone_id": "available-zone", "active": True},
            {"zone_id": "failed-zone", "active": True},
        ],
    }
    overlays = []
    monkeypatch.setattr(
        "gdelt.service.load_zone_config",
        lambda: {"scoring_version": "gdelt-event-cluster-v3", "zones": zones},
    )
    monkeypatch.setattr("gdelt.service.route_segments", lambda: [segment])
    monkeypatch.setattr("gdelt.service.ensure_schema", lambda: None)
    monkeypatch.setattr("gdelt.service.write_zone", lambda *args: None)
    monkeypatch.setattr(
        "gdelt.service.apply_segment_overlay",
        lambda route_segment, zone_results, exposed, ttl_hours: overlays.append(exposed),
    )

    result = update_news_risk(dry_run=False, client=PartiallyFailingClient())

    assert result["zonesUpdated"] == 1
    assert result["failures"] == [{"zoneId": "failed-zone", "error": "provider timeout"}]
    assert result["overlays"][0]["partialBecauseFetchFailed"] is True
    assert result["overlays"][0]["failedZoneIds"] == ["failed-zone"]
    assert overlays == [["available-zone"]]
