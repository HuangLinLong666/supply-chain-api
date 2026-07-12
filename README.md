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
