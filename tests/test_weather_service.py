from weather.service import hourly_rows

def test_hourly_weather_and_marine_merge():
    rows=hourly_rows({"hourly":{"time":["a"],"visibility":[1000]}},{"hourly":{"time":["a"],"wave_height":[2]}})
    assert rows==[{"visibility":1000,"wave_height":2}]
