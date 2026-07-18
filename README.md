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

## iiko SERVER_REST_API

`IIKO_MODE=server_rest_api` uses the read-only iikoServer REST adapter. The proven surface is authentication through `GET /resto/api/auth`, reading departments through `GET /resto/api/corporation/departments`, and logout through `GET /resto/api/logout`.

The adapter sends the iikoOffice password as a UTF-8 SHA-1 hex digest, stores the returned UUID token only in memory, and never returns or logs the password, password hash, token, or query-bearing auth/logout URLs. TLS verification is enabled by default via `IIKO_VERIFY_TLS=true`.

Still blocked in this mode: terminal groups, nomenclature, menu, orders/sales, payments, inventory, writeoffs, costs, employees, and shifts. These return `BLOCKED` with `dataset_not_implemented_for_server_rest_api`.

Example variables, without secrets:

```bash
IIKO_MODE=server_rest_api
IIKO_AUTH_TYPE=user_password
IIKO_BASE_URL=
IIKO_USERNAME=
IIKO_PASSWORD=
IIKO_API_LOGIN=
IIKO_ORGANIZATION_ID=
IIKO_VERIFY_TLS=true
IIKO_CONNECT_TIMEOUT_SECONDS=10
IIKO_READ_TIMEOUT_SECONDS=30
IIKO_MAX_RETRIES=3
```

## iiko Sales Import

S1.6.1 uses iikoServer OLAP `SALES` reports as the read-only sales source. Import a closed day:

```bash
python -m apps.core.app.cli.iiko_sync_sales --date 2026-07-16
```

Dry-run:

```bash
python -m apps.core.app.cli.iiko_sync_sales --date 2026-07-16 --dry-run
```

Daily reports are served from PostgreSQL only:

```bash
curl "http://localhost:8000/api/v1/reports/sales/daily?date=2026-07-16&organization_id=<organization-id>"
```

See `docs/iiko-sales-discovery.md` and `docs/s1-6-1-sales-import-runbook.md`.
