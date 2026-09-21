.PHONY: install dev test lint format run-api run-worker

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
