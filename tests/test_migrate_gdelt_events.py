from datetime import datetime, timezone

from scripts.migrate_gdelt_events import build_migration_plan


NOW = datetime(2026, 7, 26, 12, tzinfo=timezone.utc)


def event(article_id: str, title: str, url: str, **overrides):
    row = {
        "element_id": article_id,
        "article_id": article_id,
        "title": title,
        "url": url,
        "domain": url.split("/")[2],
        "seen_at": "2026-07-26T11:00:00+00:00",
        "zone_ids": ["red-sea"],
    }
    row.update(overrides)
    return row


def test_migration_classifies_and_clusters_without_deletion_plan():
    plan = build_migration_plan(
        [
            event("one", "Missile attack closes Red Sea shipping lane", "https://one.example/a"),
            event("two", "Red Sea shipping lane closes after missile attack", "https://two.example/b"),
        ],
        now=NOW,
    )
    assert plan["events_scanned"] == 2
    assert plan["planned_event_updates"] == 2
    assert plan["planned_cluster_creates"] == 1
    assert plan["planned_membership_creates"] == 2
    assert "delet" not in " ".join(plan).casefold()


def test_existing_cluster_and_membership_are_not_planned_again():
    first = build_migration_plan(
        [event("one", "Port congestion disrupts shipping", "https://one.example/a")],
        now=NOW,
    )
    cluster_id = first["clusters"][0]["cluster_id"]
    second = build_migration_plan(
        [event("one", "Port congestion disrupts shipping", "https://one.example/a")],
        now=NOW,
        existing_cluster_ids={cluster_id},
        existing_memberships={("one", cluster_id)},
    )
    assert second["planned_cluster_creates"] == 0
    assert second["planned_membership_creates"] == 0


def test_invalid_future_timestamp_is_audited_but_not_clustered():
    plan = build_migration_plan(
        [event("future", "War closes port", "https://one.example/future", seen_at="2026-07-27T11:00:00+00:00")],
        now=NOW,
    )
    assert plan["planned_cluster_creates"] == 0
    assert plan["rejected_counts"] == {"invalid_or_future_seen_at": 1}
