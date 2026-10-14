.PHONY: install dev test lint format run-api run-worker run-beat

install:
	pip install -e ".[dev]"

test:
	pytest -v

lint:
	ruff check .
	mypy app tests

format:
	ruff format .

run-api:
	uvicorn app.main:app --reload

run-worker:
	celery -A app.workers.celery_app worker --loglevel=info

# Milestone 14: beat is a separate process from worker on purpose — see
# docker-compose.yml's own comment on why (embedded `worker -B` would
# double-fire periodic tasks the moment a second worker replica exists).
run-beat:
	celery -A app.workers.celery_app beat --loglevel=info
