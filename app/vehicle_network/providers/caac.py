from __future__ import annotations

import os

from app.vehicle_network.models import LocationIngestRequest, LocationRecord
from app.vehicle_network.providers.base import LocationProvider


class CaacAirportProvider(LocationProvider):
    """中国民航机场适配器骨架；配置 CSV 后可直接接入正式名录。"""

    name = "caac_airports"

    async def collect(self, request: LocationIngestRequest, trace_id: str) -> list[LocationRecord]:
        csv_path = os.getenv("CAAC_AIRPORT_CSV", "")
        if not request.include_airports or "CN" not in request.country_scope or not csv_path:
            return []
        import pandas as pd

        frame = pd.read_csv(csv_path)
        records: list[LocationRecord] = []
        for row in frame.to_dict(orient="records"):
            records.append(LocationRecord.model_validate(row))
        return records
