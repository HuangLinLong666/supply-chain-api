.PHONY: install test validate-stage9 run ingest generate ais-check ais-migrate ais-worker docker-up docker-down

install:
	pip install -r requirements.txt

test:
	pytest -q

validate-stage9:
	python scripts/validate_stage9_data.py

run:
	uvicorn app.main:app --reload

ingest:
	python scripts/ingest_vehicle_locations.py

generate:
	python scripts/generate_vehicle_routes.py --origin CN-LYG --destination US-LGB

ais-check:
	python scripts/run_ais_consumer.py --check-config

ais-migrate:
	python scripts/migrate_ais_stage7.py --execute --confirm APPLY_AIS_STAGE7

ais-worker:
	python scripts/run_ais_consumer.py

docker-up:
	docker compose up --build

docker-down:
	docker compose down
