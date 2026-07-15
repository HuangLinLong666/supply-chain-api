#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from weather.config import WeatherSettings
from weather.repository import cleanup_snapshots
print({"deleted":cleanup_snapshots(WeatherSettings().retention_days)})
