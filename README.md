# supply-chain-api

FastAPI backend for querying Neo4j AuraDB supply-chain graph data.

## Local run

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## API docs

Local: http://localhost:8000/docs
Render: https://supply-chain-api.onrender.com/docs

## Main query APIs

- `GET /api/risk/segments` ranks route segments by comprehensive risk.
- `GET /api/cost/segments` ranks route segments by estimated cost.
- `GET /api/routes/recommendations` ranks complete predefined routes.
- `GET /api/routes/nodes` returns node IDs accepted by the optimizer.
- `GET /api/routes/optimize` calculates a minimum-cost, minimum-risk, or balanced path.
- `GET /api/routes/recommend` returns multiple frontend-ready routes by supplier, origin, and destination.
- `GET /api/suppliers` and `GET /api/cities` populate route-planning selectors.

Example:

```bash
curl "http://localhost:8000/api/routes/nodes?search=Port"
curl "http://localhost:8000/api/routes/recommendations?objective=min_risk&limit=5"
curl "http://localhost:8000/api/routes/optimize?origin_id=ORIGIN_ID&destination_id=DESTINATION_ID&objective=balanced&risk_weight=0.6"
curl "http://localhost:8000/api/routes/recommend?supplier=CATL&origin=Shanghai&destination=Hamburg&limit=5"
```

## Port weather risk

```bash
python scripts/update_port_weather.py --dry-run
python scripts/update_port_weather.py --port-id CNSHA
curl http://localhost:8000/api/ports/weather-risks
curl http://localhost:8000/api/ports/CNSHA/weather
pytest -q
```

Set `WEATHER_SCHEDULER_ENABLED=true` to run the Open-Meteo update every `WEATHER_UPDATE_INTERVAL_MINUTES`. See `docs/open_meteo_port_weather_risk.md`.

## Hourly GDELT dynamic route risk

`python scripts/update_gdelt_risk.py --dry-run` previews news and exposed segments. `python scripts/update_gdelt_risk.py` writes GDELT events, zone scores, and expiring route-risk overlays to AuraDB. Active coverage includes the Red Sea, Malacca Strait, Indian Ocean, Pacific Ocean, Middle East, Strait of Hormuz, South China Sea, and major ports. Route endpoints automatically avoid active HIGH/CRITICAL segments when an alternative path exists.

GitHub Actions runs `.github/workflows/update-gdelt-risk.yml` hourly. Add `AURA_NEO4J_URI`, `AURA_NEO4J_USERNAME`, `AURA_NEO4J_PASSWORD`, and `AURA_NEO4J_DATABASE` as repository Actions secrets. The workflow can also be started manually from the Actions page.

- `GET /api/risk/news/zones` returns current dynamic zone risk.
- `GET /api/risk/news` returns supporting risk news.
- `POST /api/admin/gdelt/update` triggers an authenticated update.
