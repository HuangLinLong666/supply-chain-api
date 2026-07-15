import httpx,pytest
from weather.client import OpenMeteoClient
from weather.config import WeatherSettings
from weather.risk import calculate_risk,gust_risk,precipitation_risk,risk_level,score_metrics,temperature_risk,visibility_risk,wave_risk,wind_risk

@pytest.mark.parametrize("value,low,high",[(0,0,1),(20,14,16),(40,39,41),(80,89,91)])
def test_wind_boundaries(value,low,high): assert low<=wind_risk(value)<=high
def test_gust(): assert gust_risk(90)==90
def test_precipitation(): assert precipitation_risk(30)==100
def test_visibility(): assert visibility_risk(1000)==80
def test_wave(): assert wave_risk(4)==80
def test_temperature(): assert temperature_risk(20)==5 and temperature_risk(50)>5
@pytest.mark.parametrize("score,level",[(0,"LOW"),(25,"MEDIUM"),(50,"HIGH"),(75,"CRITICAL")])
def test_levels(score,level): assert risk_level(score)==level
def test_weather_code_mapping(): assert score_metrics({"weather_code":99})["components"]["weather_code_risk"]==100
def test_missing_marine_reduces_confidence(): assert score_metrics({"wind_speed_10m":10,"wind_gusts_10m":20,"precipitation":0,"visibility":10000,"temperature_2m":20,"weather_code":0})["confidence"]<1
def test_missing_visibility_is_not_zero_risk():
    result=score_metrics({"wind_speed_10m":40,"wave_height":2}); assert result["score"]>0 and result["data_completeness"]<1
def test_weight_renormalization(): assert score_metrics({"wave_height":4})["score"]==80
def test_forecast_windows_and_trend():
    result=calculate_risk({"wind_speed_10m":5,"wave_height":0.5},[{"wind_speed_10m":90,"wave_height":5}]*24); assert result["max_risk_6h"]==result["max_risk_24h"] and result["trend"] in {"WORSENING","RAPIDLY_WORSENING"}

def test_http_429_retry(monkeypatch):
    calls={"n":0}
    def handler(request):
        calls["n"]+=1
        return httpx.Response(429 if calls["n"]==1 else 200,json={"ok":True})
    settings=WeatherSettings(max_retries=1)
    client=OpenMeteoClient(settings,httpx.MockTransport(handler)); monkeypatch.setattr("time.sleep",lambda _:None)
    assert client._get("https://example.test",{})=={"ok":True}; assert calls["n"]==2

def test_invalid_json_retried(monkeypatch):
    client=OpenMeteoClient(WeatherSettings(max_retries=0),httpx.MockTransport(lambda request:httpx.Response(200,text="bad")))
    with pytest.raises(RuntimeError): client._get("https://example.test",{})

def test_timeout(monkeypatch):
    client=OpenMeteoClient(WeatherSettings(max_retries=0),httpx.MockTransport(lambda request:(_ for _ in ()).throw(httpx.ReadTimeout("timeout"))))
    with pytest.raises(RuntimeError): client._get("https://example.test",{})
