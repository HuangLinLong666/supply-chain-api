import json
from datetime import datetime, timezone

from ais.config import AisSettings, AisTarget, BoundingBox
from ais.service import AisConsumer, DedupeCache


class FakeStorage:
    def __init__(self):
        self.observations = []
        self.snapshots = []
        self.states = []

    def ensure_schema(self):
        return None

    def sync_targets(self, targets):
        return None

    def upsert_latest_observations(self, observations):
        self.observations.extend(observations)
        return len(observations)

    def write_snapshots(self, snapshots):
        self.snapshots.extend(snapshots)
        return {"snapshots": len(snapshots), "segments": 2 if snapshots else 0}

    def update_provider_state(self, **properties):
        self.states.append(properties)


def make_consumer(*, dry_run: bool = False) -> AisConsumer:
    settings = AisSettings(
        api_key="super-secret-key",
        window_minutes=60,
        snapshot_ttl_minutes=90,
        dedupe_ttl_seconds=60,
        dedupe_max_entries=100,
    )
    target = AisTarget(
        target_id="test-port",
        name="测试港",
        target_type="port",
        port_ids=("TST",),
        aggregation_bbox=BoundingBox(0, 0, 1, 1),
        subscription_bbox=BoundingBox(-1, -1, 2, 2),
    )
    return AisConsumer(settings=settings, targets=[target], storage=FakeStorage(), dry_run=dry_run)


def position_message() -> dict:
    return {
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": 123456789, "ShipName": "REAL SHIP", "time_utc": "2026-07-26T12:10:00Z"},
        "Message": {
            "PositionReport": {
                "UserID": 123456789,
                "Latitude": 0.5,
                "Longitude": 0.5,
                "Sog": 0.4,
                "Cog": 90,
                "NavigationalStatus": 1,
            }
        },
    }


def test_dedupe_cache_bounds_entries_and_detects_duplicates():
    cache = DedupeCache(ttl_seconds=10, max_entries=2)
    assert not cache.seen("a", now=1)
    assert cache.seen("a", now=2)
    assert not cache.seen("b", now=3)
    assert not cache.seen("c", now=4)
    assert not cache.seen("a", now=5)


def test_consumer_coalesces_database_writes_and_never_exposes_key():
    consumer = make_consumer()
    received_at = datetime(2026, 7, 26, 12, 10, tzinfo=timezone.utc)
    assert consumer.process_message(position_message(), received_at=received_at)
    assert not consumer.process_message(position_message(), received_at=received_at)

    result = consumer.flush(datetime(2026, 7, 26, 12, 20, tzinfo=timezone.utc))

    assert result["observations"] == 1
    assert result["snapshots"] == 1
    assert result["segments"] == 2
    assert consumer.counters.duplicates_ignored == 1
    assert len(consumer.storage.observations) == 1
    public_json = json.dumps(consumer.public_configuration())
    assert "super-secret-key" not in public_json
    assert consumer.public_configuration()["apiKeyExposed"] is False


def test_fixture_style_dry_run_never_calls_storage():
    consumer = make_consumer(dry_run=True)
    consumer.process_message(position_message(), received_at=datetime(2026, 7, 26, 12, 10, tzinfo=timezone.utc))
    result = consumer.flush(datetime(2026, 7, 26, 12, 20, tzinfo=timezone.utc))
    assert result["dryRun"] is True
    assert result["observations"] == 1
    assert consumer.storage.observations == []
