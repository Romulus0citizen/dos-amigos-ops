.PHONY: install run test lint typecheck check up down migrate

install:
	python -m pip install -e ".[dev]"

run:
	uvicorn apps.core.app.main:app --reload

test:
	pytest

lint:
	ruff check .

typecheck:
	mypy apps integrations

check:
	python -m compileall apps integrations tests
	ruff check .
	mypy apps integrations
	pytest

up:
	docker compose up --build

down:
	docker compose down

migrate:
	alembic upgrade head
