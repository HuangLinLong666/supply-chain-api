from __future__ import annotations

import json
from pathlib import Path

from app.vehicle_network.models import LocationIngestRequest, LocationRecord, SourceType
from app.vehicle_network.providers.base import LocationProvider


class SampleRegistryProvider(LocationProvider):
    """可离线运行的示例注册表；后续可替换为 UN/LOCODE 下载器。"""

    name = "sample_unlocode_registry"

    def __init__(self, path: str = "data/sample_locations.json"):
        self.path = Path(path)

    async def collect(self, request: LocationIngestRequest, trace_id: str) -> list[LocationRecord]:
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        allowed_kinds = set()
        if request.include_ports:
            allowed_kinds.add("port")
        if request.include_airports:
            allowed_kinds.add("airport")
        if request.include_rail_terminals:
            allowed_kinds.add("rail_terminal")
        if request.include_road_terminals:
            allowed_kinds.add("road_terminal")
        return [
            LocationRecord(
                **row,
                source="项目内置地点示例数据",
                source_url="https://unece.org/trade/cefact/unlocode-code-list-country-and-territory",
                source_type=SourceType.FABRICATED_FOR_TESTING,
                confidence=0.2,
                is_inferred=True,
            )
            for row in rows
            if row["country_code"] in request.country_scope and row["kind"] in allowed_kinds
        ]
