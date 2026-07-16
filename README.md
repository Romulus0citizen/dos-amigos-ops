# Dos Amigos Ops

Deterministic operational core for Dos Amigos.

Hermes is the management interface and agent layer. This repository is the source of truth for integrations, raw data ingestion, calculations, audit, and controlled actions.

## Sprint 1 scaffold

- FastAPI service
- PostgreSQL
- Alembic
- health endpoints
- iiko adapter contract and mock adapter
- secret redaction
- Docker Compose
- CI
- architecture documents

Production Hermes is not changed by this scaffold.

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

Check:

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

API docs: `http://localhost:8000/docs`

## Local Python

```bash
python -m venv .venv
source .venv/bin/activate
# Windows: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
uvicorn apps.core.app.main:app --reload
```

## Checks

```bash
python -m compileall apps integrations tests
ruff check .
mypy apps integrations
pytest
```

## Secrets

Never commit `.env`, passwords, API credentials, tokens, license keys, authorization headers, or unredacted production payloads.
