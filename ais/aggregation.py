from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ais.config import AisTarget
from ais.models import NormalizedAisObservation, utc_now


AIS_SCORING_VERSION = "ais-port-congestion-v1"
ANCHORED_NAVIGATION_STATUSES = {1, 5}


@dataclass
class _TargetVesselState:
    inside: bool
    observed_at: datetime


@dataclass(frozen=True)
class PortTrafficSnapshot:
    snapshot_id: str
    target_id: str
    target_name: str
    target_type: str
    window_start: datetime
    window_end: datetime
    vessel_count: int
    anchored_count: int
    average_speed: float | None
    arrival_count: int
    departure_count: int
    congestion_score: float
    observation_count: int
    speed_coverage: float
    navigation_status_coverage: float
    temporal_coverage: float
    data_completeness: float
    confidence: float
    observed_at: datetime
    generated_at: datetime
    expires_at: datetime
    score_components: dict[str, float | None]
    sample_mmsis: tuple[str, ...]

    def storage_row(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "observation_id": self.snapshot_id,
            "evidence_id": self.snapshot_id,
            "target_id": self.target_id,
            "target_name": self.target_name,
            "target_type": self.target_type,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "vessel_count": self.vessel_count,
            "anchored_count": self.anchored_count,
            "average_speed": self.average_speed,
            "arrival_count": self.arrival_count,
            "departure_count": self.departure_count,
            "congestion_score": self.congestion_score,
            "congestion_score_normalized": round(self.congestion_score / 100, 6),
            "observation_count": self.observation_count,
            "speed_coverage": self.speed_coverage,
            "navigation_status_coverage": self.navigation_status_coverage,
            "temporal_coverage": self.temporal_coverage,
            "data_completeness": self.data_completeness,
            "confidence": self.confidence,
            "observed_at": self.observed_at.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "score_components_json": json.dumps(self.score_components, ensure_ascii=False, sort_keys=True),
            "sample_mmsis": list(self.sample_mmsis),
            "provider": "AISStream.io",
            "source": "AISStream.io WebSocket",
            "source_url": "https://aisstream.io/documentation.html",
            "source_type": "ais_observed",
            "data_status": "estimated",
            "calculation_status": "derived_from_observed_ais",
            "is_inferred": True,
            "status": "available",
            "scoring_version": AIS_SCORING_VERSION,
            "schema_version": "ais-port-traffic-v1",
        }


class PortTrafficAggregator:
    def __init__(
        self,
        targets: list[AisTarget],
        *,
        window_minutes: int = 60,
        snapshot_ttl_minutes: int = 90,
    ) -> None:
        self.targets = {target.target_id: target for target in targets}
        self.window = timedelta(minutes=window_minutes)
        self.snapshot_ttl = timedelta(minutes=snapshot_ttl_minutes)
        self._states: dict[str, dict[str, _TargetVesselState]] = defaultdict(dict)
        self._latest_inside: dict[str, dict[str, NormalizedAisObservation]] = defaultdict(dict)
        self._position_events: dict[str, deque[tuple[datetime, str]]] = defaultdict(deque)
        self._arrivals: dict[str, deque[datetime]] = defaultdict(deque)
        self._departures: dict[str, deque[datetime]] = defaultdict(deque)

    def ingest(self, observation: NormalizedAisObservation) -> tuple[str, ...]:
        if not observation.has_position:
            return ()
        assert observation.latitude is not None
        assert observation.longitude is not None
        matched_targets: list[str] = []
        for target in self.targets.values():
            if not target.subscription_bbox.contains(observation.latitude, observation.longitude):
                continue
            matched_targets.append(target.target_id)
            inside = target.aggregation_bbox.contains(observation.latitude, observation.longitude)
            previous = self._states[target.target_id].get(observation.mmsi)
            if previous is not None and observation.observed_at < previous.observed_at:
                continue
            if previous is not None and previous.inside != inside:
                events = self._arrivals if inside else self._departures
                events[target.target_id].append(observation.observed_at)
            self._states[target.target_id][observation.mmsi] = _TargetVesselState(
                inside=inside,
                observed_at=observation.observed_at,
            )
            if inside:
                self._latest_inside[target.target_id][observation.mmsi] = observation
            else:
                self._latest_inside[target.target_id].pop(observation.mmsi, None)
            self._position_events[target.target_id].append((observation.observed_at, observation.mmsi))
        return tuple(sorted(matched_targets))

    def _window_bounds(self, now: datetime) -> tuple[datetime, datetime]:
        current = now.astimezone(timezone.utc)
        window_seconds = int(self.window.total_seconds())
        epoch_seconds = int(current.timestamp())
        start_seconds = epoch_seconds - epoch_seconds % window_seconds
        start = datetime.fromtimestamp(start_seconds, tz=timezone.utc)
        return start, start + self.window

    def _prune(self, target_id: str, cutoff: datetime) -> None:
        for queue in (self._position_events[target_id],):
            while queue and queue[0][0] < cutoff:
                queue.popleft()
        for queue in (self._arrivals[target_id], self._departures[target_id]):
            while queue and queue[0] < cutoff:
                queue.popleft()
        stale_mmsis = [
            mmsi
            for mmsi, state in self._states[target_id].items()
            if state.observed_at < cutoff - self.window
        ]
        for mmsi in stale_mmsis:
            self._states[target_id].pop(mmsi, None)
            self._latest_inside[target_id].pop(mmsi, None)

    def build_snapshots(self, now: datetime | None = None) -> list[PortTrafficSnapshot]:
        generated_at = (now or utc_now()).astimezone(timezone.utc)
        window_start, window_end = self._window_bounds(generated_at)
        snapshots: list[PortTrafficSnapshot] = []
        for target in self.targets.values():
            self._prune(target.target_id, window_start)
            events = self._position_events[target.target_id]
            if not events:
                continue
            active = {
                mmsi: observation
                for mmsi, observation in self._latest_inside[target.target_id].items()
                if observation.position_observed_at is not None and observation.position_observed_at >= window_start
            }
            observations = list(active.values())
            speeds = [observation.speed_knots for observation in observations if observation.speed_knots is not None]
            navigation_codes = [
                observation.navigational_status_code
                for observation in observations
                if observation.navigational_status_code is not None
            ]
            anchored_count = sum(code in ANCHORED_NAVIGATION_STATUSES for code in navigation_codes)
            slow_count = sum(speed <= target.slow_speed_knots for speed in speeds)
            vessel_count = len(active)
            arrivals = len(self._arrivals[target.target_id])
            departures = len(self._departures[target.target_id])
            first_observed_at = min(item[0] for item in events)
            last_observed_at = max(item[0] for item in events)
            temporal_coverage = min(
                max((last_observed_at - first_observed_at).total_seconds() / self.window.total_seconds(), 0.0),
                1.0,
            )
            speed_coverage = len(speeds) / vessel_count if vessel_count else 0.0
            navigation_coverage = len(navigation_codes) / vessel_count if vessel_count else 0.0
            density_pressure = min(vessel_count / target.congestion_reference_vessel_count, 1.0)
            anchored_ratio = anchored_count / len(navigation_codes) if navigation_codes else None
            slow_ratio = slow_count / len(speeds) if speeds else None
            transition_total = arrivals + departures
            arrival_imbalance = (
                max(arrivals - departures, 0) / transition_total
                if temporal_coverage >= 0.25 and transition_total
                else 0.0
                if temporal_coverage >= 0.25
                else None
            )
            components = {
                "densityPressure": round(density_pressure, 6),
                "anchoredRatio": round(anchored_ratio, 6) if anchored_ratio is not None else None,
                "slowMovingRatio": round(slow_ratio, 6) if slow_ratio is not None else None,
                "arrivalImbalance": round(arrival_imbalance, 6) if arrival_imbalance is not None else None,
            }
            weighted_components = [
                (density_pressure, 0.35),
                (anchored_ratio, 0.30),
                (slow_ratio, 0.25),
                (arrival_imbalance, 0.10),
            ]
            used = [(value, weight) for value, weight in weighted_components if value is not None]
            total_weight = sum(weight for _, weight in used)
            congestion_score = 100 * sum(value * weight for value, weight in used) / total_weight
            completeness = round((1.0 + speed_coverage + navigation_coverage + temporal_coverage) / 4, 4)
            sample_confidence = min(len(set(mmsi for _, mmsi in events)) / 20, 1.0)
            confidence = round(
                0.35 * sample_confidence
                + 0.35 * temporal_coverage
                + 0.15 * speed_coverage
                + 0.15 * navigation_coverage,
                4,
            )
            snapshots.append(
                PortTrafficSnapshot(
                    snapshot_id=f"aisstream:{target.target_id}:{window_start.strftime('%Y%m%dT%H%MZ')}",
                    target_id=target.target_id,
                    target_name=target.name,
                    target_type=target.target_type,
                    window_start=window_start,
                    window_end=window_end,
                    vessel_count=vessel_count,
                    anchored_count=anchored_count,
                    average_speed=round(sum(speeds) / len(speeds), 4) if speeds else None,
                    arrival_count=arrivals,
                    departure_count=departures,
                    congestion_score=round(congestion_score, 2),
                    observation_count=len(events),
                    speed_coverage=round(speed_coverage, 4),
                    navigation_status_coverage=round(navigation_coverage, 4),
                    temporal_coverage=round(temporal_coverage, 4),
                    data_completeness=completeness,
                    confidence=confidence,
                    observed_at=last_observed_at,
                    generated_at=generated_at,
                    expires_at=last_observed_at + self.snapshot_ttl,
                    score_components=components,
                    sample_mmsis=tuple(sorted(active)[:25]),
                )
            )
        return snapshots
