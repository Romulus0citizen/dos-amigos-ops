# S1.6.1 Sales Import Runbook

## Purpose

Import one or more closed business days of read-only iikoServer OLAP sales aggregates into Dos Amigos Core and serve daily sales reports from PostgreSQL.

## Requirements

- Python 3.12 environment.
- PostgreSQL with Alembic migrations applied.
- Existing S1.5 iiko auth settings.
- `BUSINESS_TIMEZONE` configured for restaurant business-day validation.
- TLS verification enabled.

## Local Mock/Fixture Checks

```bash
.venv312/bin/pytest tests/test_iiko_sales_normalization.py tests/test_iiko_sales_persistence_api.py -q
```

## Dry Run

```bash
python -m apps.core.app.cli.iiko_sync_sales --date 2026-07-16 --dry-run
```

Dry-run authenticates, fetches, parses, and validates but writes nothing to the database.

## Import One Day

```bash
python -m apps.core.app.cli.iiko_sync_sales --date 2026-07-16
```

Open business days are rejected unless `--allow-open-day` is explicitly provided.

## Import Date Range

```bash
python -m apps.core.app.cli.iiko_sync_sales --from-date 2026-07-01 --to-date 2026-07-16
```

Ranges are processed sequentially and must not exceed 31 days.

## Get Daily Report

```bash
curl "http://localhost:8000/api/v1/reports/sales/daily?date=2026-07-16&organization_id=<organization-id>"
```

The endpoint reads PostgreSQL only. It never calls iiko.

## Status Interpretation

- `PROVEN`: core OLAP source, dates, organization, gross/net sales, checks, payments, and product totals validated.
- `PARTIAL`: confirmed revenue is usable, but optional dimensions or reconciliation have caveats.
- `BLOCKED`: iiko returned an explicit permission/contract block.
- `UNKNOWN`: response structure, organization, date, totals, or enum values are unsafe to trust.

## Repeat Import

The importer calculates a canonical SHA-256 checksum from normalized rows. Repeating identical data creates no duplicates and records an unchanged sync run. Repeating changed data replaces the previous day atomically.

## Recovery After Error

If validation or persistence fails before commit, previous successful data remains unchanged. Failed sync runs contain sanitized error codes and messages only.

## Safe Production Check

Run one closed day first, preferably with `--dry-run`. Do not print `.env`, tokens, raw responses, or database URLs.

## Forbidden

- Do not disable TLS.
- Do not call iiko write endpoints.
- Do not import PII.
- Do not edit production `.env`, containers, ports, Hermes, firewall, or deployment.
