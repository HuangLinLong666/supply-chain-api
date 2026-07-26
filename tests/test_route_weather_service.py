from datetime import datetime, timedelta, timezone

from weather.route_service import update_route_weather


class FakeClient:
    def _payload(self, points, marine=False):
        start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        times = [(start + timedelta(hours=index)).isoformat() for index in range(4)]
        result = []
        for _ in points:
            if marine:
                result.append({"hourly": {"time": times, "wave_height": [1, 1, 1, 1]}})
            else:
                result.append(
                    {
                        "hourly": {
                            "time": times,
                            "wind_speed_10m": [10, 10, 10, 10],
                            "wind_gusts_10m": [15, 15, 15, 15],
                            "precipitation": [0, 0, 0, 0],
                            "visibility": [20000, 20000, 20000, 20000],
                            "weather_code": [0, 0, 0, 0],
                            "temperature_2m": [20, 20, 20, 20],
                            "snowfall": [0, 0, 0, 0],
                        }
                    }
                )
        return result

    def weather_batch(self, points, forecast_hours=24):
        return self._payload(points)

    def marine_batch(self, points, forecast_hours=24):
        return self._payload(points, marine=True)


def test_route_service_deduplicates_points_and_builds_write_rows(monkeypatch):
    segments = [
        {
            "element_id": "one",
            "segment_id": "SEA-1",
            "mode": "sea",
            "from_lat": 31.23,
            "from_lng": 121.47,
            "to_lat": 1.35,
            "to_lng": 103.82,
            "from_country": "China",
            "to_country": "Singapore",
            "from_labels": ["Port"],
            "to_labels": ["Port"],
            "duration_hours": 1,
        },
        {
            "element_id": "two",
            "segment_id": "AIR-1",
            "mode": "air",
            "from_lat": 31.23,
            "from_lng": 121.47,
            "to_lat": 1.35,
            "to_lng": 103.82,
            "from_country": "China",
            "to_country": "Singapore",
            "from_labels": ["Airport"],
            "to_labels": ["Airport"],
            "duration_hours": 1,
        },
    ]
    captured = []
    monkeypatch.setattr("weather.route_service.list_route_segments", lambda segment_ids=None: segments)
    monkeypatch.setattr("weather.route_service.ensure_schema", lambda: None)
    monkeypatch.setattr(
        "weather.route_service.write_route_weather",
        lambda rows, dry_run=False: captured.extend(rows) or len(rows),
    )
    result = update_route_weather(dry_run=False, client=FakeClient())
    assert result["uniqueWeatherPoints"] == 2
    assert result["uniqueMarinePoints"] == 2
    assert result["segmentsWritten"] == 2
    assert {row["mode"] for row in captured} == {"sea", "air"}
    assert all(row["sampling_method"] == "endpoint_fallback" for row in captured)
