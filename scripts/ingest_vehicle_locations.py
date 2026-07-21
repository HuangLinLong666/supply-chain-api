from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.vehicle_network.models import LocationIngestRequest
from app.vehicle_network.services import LocationIngestionService


async def run() -> None:
    parser = argparse.ArgumentParser(description="批量采集整车运输地点")
    parser.add_argument("--countries", default="CN,US,DE,BR,MX,AE")
    args = parser.parse_args()
    request = LocationIngestRequest(country_scope=[item.strip().upper() for item in args.countries.split(",")])
    result = await LocationIngestionService().ingest(request, f"cli_{uuid4().hex}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
