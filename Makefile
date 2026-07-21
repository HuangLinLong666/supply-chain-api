.PHONY: install test run ingest generate docker-up docker-down

install:
	pip install -r requirements.txt

test:
	pytest -q

run:
	uvicorn app.main:app --reload

ingest:
	python scripts/ingest_vehicle_locations.py

generate:
	python scripts/generate_vehicle_routes.py --origin CN-LYG --destination US-LGB

docker-up:
	docker compose up --build

docker-down:
	docker compose down
