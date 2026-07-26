from datetime import datetime, timezone

from ais.aggregation import PortTrafficAggregator
from ais.config import AisTarget, BoundingBox
from ais.models import NormalizedAisObservation


def target() -> AisTarget:
    return AisTarget(
        target_id="test-port",
        name="测试港",
        target_type="port",
        port_ids=("TST",),
        aggregation_bbox=BoundingBox(0, 0, 1, 1),
        subscription_bbox=BoundingBox(-1, -1, 2, 2),
        congestion_reference_vessel_count=10,
        slow_speed_knots=1,
    )


def observation(
    mmsi: str,
    minute: int,
    latitude: float,
    longitude: float,
    *,
    speed: float | None = None,
    navigation_status: int | None = None,
) -> NormalizedAisObservation:
    observed_at = datetime(2026, 7, 26, 12, minute, tzinfo=timezone.utc)
    return NormalizedAisObservation(
        mmsi=mmsi,
        message_type="PositionReport",
        observed_at=observed_at,
        received_at=observed_at,
        dedupe_key=f"{mmsi}-{minute}",
        latitude=latitude,
        longitude=longitude,
        speed_knots=speed,
        navigational_status_code=navigation_status,
        navigational_status="at_anchor" if navigation_status == 1 else None,
        position_observed_at=observed_at,
        message_types=("PositionReport",),
    )


def test_no_real_position_observation_produces_no_snapshot():
    aggregator = PortTrafficAggregator([target()])
    assert aggregator.build_snapshots(datetime(2026, 7, 26, 12, 30, tzinfo=timezone.utc)) == []


def test_real_positions_create_window_aggregate_and_transitions():
    aggregator = PortTrafficAggregator([target()], window_minutes=60, snapshot_ttl_minutes=90)
    aggregator.ingest(observation("123456789", 5, -0.5, 0.5, speed=5))
    aggregator.ingest(observation("123456789", 10, 0.5, 0.5, speed=0.4, navigation_status=1))
    aggregator.ingest(observation("987654321", 15, 0.6, 0.6, speed=0.2, navigation_status=5))
    aggregator.ingest(observation("123456789", 30, 1.5, 0.5, speed=4, navigation_status=0))

    snapshots = aggregator.build_snapshots(datetime(2026, 7, 26, 12, 45, tzinfo=timezone.utc))

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.vessel_count == 1
    assert snapshot.anchored_count == 1
    assert snapshot.average_speed == 0.2
    assert snapshot.arrival_count == 1
    assert snapshot.departure_count == 1
    assert snapshot.observation_count == 4
    assert 0 < snapshot.congestion_score <= 100
    assert snapshot.observed_at == datetime(2026, 7, 26, 12, 30, tzinfo=timezone.utc)
    assert snapshot.expires_at == datetime(2026, 7, 26, 14, 0, tzinfo=timezone.utc)
    assert snapshot.storage_row()["calculation_status"] == "derived_from_observed_ais"


def test_out_of_order_position_does_not_replace_latest_or_inflate_count():
    aggregator = PortTrafficAggregator([target()], window_minutes=60)
    aggregator.ingest(observation("123456789", 20, 0.5, 0.5, speed=2))
    aggregator.ingest(observation("123456789", 10, 1.5, 0.5, speed=8))

    snapshot = aggregator.build_snapshots(datetime(2026, 7, 26, 12, 30, tzinfo=timezone.utc))[0]

    assert snapshot.vessel_count == 1
    assert snapshot.observation_count == 1
    assert snapshot.average_speed == 2
    assert snapshot.departure_count == 0
