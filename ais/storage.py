from __future__ import annotations

from typing import Any, Protocol

from ais.aggregation import PortTrafficSnapshot
from ais.config import AisTarget
from ais.models import NormalizedAisObservation


class AisStorage(Protocol):
    """Persistence boundary for replacing Neo4j latest-state storage later."""

    def ensure_schema(self) -> None: ...

    def sync_targets(self, targets: list[AisTarget]) -> None: ...

    def upsert_latest_observations(self, observations: list[NormalizedAisObservation]) -> int: ...

    def write_snapshots(self, snapshots: list[PortTrafficSnapshot]) -> dict[str, Any]: ...

    def update_provider_state(self, **properties: Any) -> None: ...
