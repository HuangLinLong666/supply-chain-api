from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.vehicle_network.models import RouteGenerateRequest
from app.vehicle_network.services import RouteGenerationService


def main() -> None:
    parser = argparse.ArgumentParser(description="生成整车运输候选路径")
    parser.add_argument("--origin", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--strategy", default="hybrid", choices=["hybrid", "min_risk", "min_cost", "fastest"])
    parser.add_argument("--no-persist", action="store_true")
    args = parser.parse_args()
    request = RouteGenerateRequest(origin=args.origin, destination=args.destination, ranking_strategy=args.strategy, persist=not args.no_persist)
    result = RouteGenerationService().generate(request, f"cli_{uuid4().hex}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
