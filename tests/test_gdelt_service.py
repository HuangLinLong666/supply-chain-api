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
