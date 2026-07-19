# S1.6.2 Stage A — Daily COO Report

## Architecture

Stage A keeps delivery split between Core and the existing Telegram bot environment.

- Core builds deterministic daily sales reports from `iiko_sales_daily`,
  `iiko_sales_daily_payments`, and `iiko_sales_daily_products`.
- Core stores ready report text in `hermes_report_outbox`.
- Core exposes a protected internal outbox contract under
  `/api/v1/internal/report-outbox`.
- `ops/telegram/report_sender.py` runs outside Core, reads pending reports through
  the Core API, and sends them through Telegram to every `ALLOWED_IDS` recipient.
- Core never receives `BOT_TOKEN` or Telegram IDs.
- Hermes delivery remains disabled with `HERMES_DELIVERY_MODE=disabled`.

No database migration is required for Stage A because `hermes_report_outbox`
already has the payload, delivery status, attempt, delivered timestamp, and
redacted error fields needed by the sender.

## Environment

Core:

```bash
REPORT_OUTBOX_INTERNAL_TOKEN=
HERMES_DELIVERY_MODE=disabled
```

Telegram sender environment:

```bash
BOT_ENV_FILE=/opt/hermes-bots/dos-amigos/.env
BOT_TOKEN=
ALLOWED_IDS=
CORE_URL=http://127.0.0.1:8090
REPORT_OUTBOX_INTERNAL_TOKEN=
```

`BOT_TOKEN` and `ALLOWED_IDS` must stay only in the Telegram bot environment.
Do not add them to Core `.env` files or repository files.

`REPORT_OUTBOX_INTERNAL_TOKEN` is required. If it is not configured, the
internal API fails closed with `503 internal API not configured`. Missing or
wrong `Authorization: Bearer ...` returns `401`.

## Manual Run

Run one delivery pass from an environment that already contains the bot secrets:

```bash
ops/bin/send-telegram-reports.sh --date 2026-07-16 --json
```

Before the first Stage A delivery for a date, rebuild the existing outbox
payload so old queued rows receive the current COO report text without creating
a duplicate:

```bash
python -m apps.core.app.cli.iiko_sales_automation \
  --date YYYY-MM-DD \
  --rebuild-outbox \
  --json
```

The sender marks a report as delivered only after every allowed recipient
receives every message chunk.

## Dry Run

Dry-run reads pending reports and prints a safe aggregate summary. It does not
send Telegram messages and does not mutate `hermes_report_outbox`.

```bash
ops/bin/send-telegram-reports.sh --date 2026-07-16 --dry-run --json
```

## Recovery

If delivery fails for at least one recipient, the sender calls
`/{report_id}/failed`. Core increments `delivery_attempts`, stores a redacted
error, and does not mark the report delivered.

Recovery options for Stage A:

- inspect the safe outbox status through Core or database read-only tooling;
- fix the external issue, such as Telegram availability or recipient access;
- rerun the sender with `--retry-failed` to explicitly retry failed rows.

Stage A does not create a new outbox row during sender retries.

Stage A provides at-least-once delivery. After Core successfully records
`delivered`, a normal repeated run does not send the report again. Partial
delivery, or an error while recording `delivered` after Telegram accepted the
message, can lead to a repeated send. Recipient-level exactly-once delivery is a
Stage B concern and requires recipient-level tracking or delivery to a shared
Telegram chat.

## Acceptance

- A pending outbox report can be fetched through the internal API with the
  shared token.
- The report text is deterministic and Russian-language.
- Previous-day and previous-weekday comparisons use stored sales only.
- Missing comparison days render as `нет данных для сравнения`.
- Zero comparison revenue does not raise a division error.
- The sender delivers to all `ALLOWED_IDS` before marking delivered.
- Re-running the sender after delivery does not resend the delivered report.
- Failed reports are retried only with `--retry-failed`.
- Dry-run does not send or mutate anything.
- Failures are recorded without Telegram tokens or chat IDs.

## Not In Stage A

- automatic 23:30 report schedule;
- 06:00 reconciliation workflow;
- historical backfill workflow;
- production deployment or systemd timer creation.
