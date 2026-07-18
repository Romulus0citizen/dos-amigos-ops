# S1.6.1a Sales Automation Runbook

## Architecture

Sales automation is a one-shot CLI invoked by a host-level systemd timer. FastAPI does not start a scheduler, background thread, or loop.

Flow:

1. systemd timer starts `ops/bin/run-sales-sync.sh`.
2. The script takes a host `flock`.
3. The CLI takes a PostgreSQL advisory lock.
4. The app decides whether work is due.
5. Missing closed days are imported sequentially.
6. A deterministic Hermes outbox record is created for each imported daily report.

## Settings

- `SALES_AUTOMATION_ENABLED=false`
- `SALES_DAILY_RUN_LOCAL_TIME=06:00`
- `SALES_BACKFILL_MAX_DAYS=14`
- `SALES_RETRY_MAX_ATTEMPTS=4`
- `SALES_RETRY_BASE_SECONDS=30`
- `SALES_OUTBOX_ENABLED=true`
- `HERMES_DELIVERY_MODE=disabled`

`SALES_DAILY_RUN_LOCAL_TIME` is interpreted in `BUSINESS_TIMEZONE`.

## Manual Run

```bash
python -m apps.core.app.cli.iiko_sales_automation --date 2026-07-16 --json
```

## Run Due

```bash
python -m apps.core.app.cli.iiko_sales_automation --run-due --json
```

The command exits with code 0 for `not_due`, `already_completed`, and `already_running`.

## Backfill

```bash
python -m apps.core.app.cli.iiko_sales_automation --backfill --backfill-days 7 --json
```

Backfill processes closed days only, oldest to newest. `partial` days are not retried unless `--retry-partial` is provided.

## Advisory Lock

The application uses a stable PostgreSQL advisory lock key derived from `dos-amigos:iiko-sales-automation`. It never uses Python `hash()`.

If the lock is occupied, the run returns `already_running` and does not import.

## Retry

The confirmed iiko transport retries only safe read-only report requests for transient failures such as timeout, connection errors, 429, 502, 503, and 504. Auth and logout remain non-retried.

## Outbox

`hermes_report_outbox` stores `payload_json`, `payload_markdown`, and an idempotency key:

```text
sales_daily:{organization_id}:{business_date}:{source_checksum}
```

Identical checksums do not create duplicates. A changed checksum creates a new pending record and supersedes older pending records for the same day. Delivered records are kept.

## Rebuild Outbox

```bash
python -m apps.core.app.cli.iiko_sales_automation --rebuild-outbox --json
```

This reads imported sales days and recreates missing outbox rows without calling iiko.

## Disabled Hermes Mode

With `HERMES_DELIVERY_MODE=disabled`, outbox rows remain `pending` and no external delivery is attempted.

```bash
python -m apps.core.app.cli.publish_hermes_reports --json
```

## Mock Delivery

For local checks only:

```bash
HERMES_DELIVERY_MODE=mock python -m apps.core.app.cli.publish_hermes_reports --json
```

Mock mode marks records delivered with synthetic external IDs.

## Systemd Installation

Operators may copy:

- `ops/systemd/dos-amigos-sales-sync.service`
- `ops/systemd/dos-amigos-sales-sync.timer`
- `ops/bin/run-sales-sync.sh`

Then run standard `systemctl enable --now dos-amigos-sales-sync.timer` after verifying the service account and release path. The repository does not install these files automatically.

## Systemd Removal

Disable the timer and remove copied unit files through normal systemd operations. Do not delete application data.

## Status

```bash
curl "http://localhost:8000/api/v1/operations/sales-automation/status"
```

The endpoint reads PostgreSQL only.

## Diagnostics

Check automation runs in `iiko_sales_automation_runs` and queued delivery in `hermes_report_outbox`. Logs use sanitized event names and do not include tokens, passwords, database URLs, or raw payloads.

## Rollback

Rollback the code and Alembic migration through normal release procedures. Delivered outbox records should not be deleted manually.

## Forbidden

- Do not run scheduler inside FastAPI.
- Do not disable TLS.
- Do not call iiko write endpoints.
- Do not read or modify production Hermes secrets.
- Do not call Telegram APIs directly.
- Do not deploy from this task.
