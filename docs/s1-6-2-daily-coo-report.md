# S1.6.2 — Daily COO Report

## Architecture

Core owns deterministic sales data, outbox rows, reconciliation decisions, and
recipient delivery state. The Telegram sender owns `BOT_TOKEN`, `ALLOWED_IDS`,
`REPORT_RECIPIENT_KEY_SECRET`, and message delivery. Core never receives
Telegram tokens or raw Telegram IDs.

`HERMES_DELIVERY_MODE=disabled` remains the safe default. Delivery goes through
the separate sender and the protected internal API under
`/api/v1/internal/report-outbox`.

Production execution is split deliberately:

1. systemd runs host wrapper `ops/bin/run-daily-coo-report.sh`;
2. the wrapper runs Core CLI inside Docker Compose;
3. Core synchronizes iiko, writes `daily_coo_report_runs`, and creates or
   updates outbox rows;
4. the wrapper then runs the host Telegram sender for the returned
   `business_date` with `--retry-failed`.

Core CLI must not read `BOT_TOKEN`, `ALLOWED_IDS`, `/opt/hermes-bots`, or invoke
the host sender.

## Stage A Acceptance

- Core creates human-facing Russian COO report text from stored iiko sales data.
- Manual sender delivery uses `BOT_TOKEN` and `ALLOWED_IDS` from the bot
  environment only.
- `REPORT_OUTBOX_INTERNAL_TOKEN` is required; missing configuration fails closed.
- A delivered report is not sent again by a normal repeated run.
- Stage A provides at-least-once delivery, not recipient-level exactly-once.

## Stage B Closeout 23:30

The closeout run is:

```bash
ops/bin/run-daily-coo-report.sh --closeout --json
```

Rules:

- scheduled daily at `23:30 Asia/Yekaterinburg`;
- `business_date` is the current local date;
- iiko sales are imported again for that open business date;
- the outbox row is created or updated with the operational title
  `Dos Amigos — оперативные итоги DD.MM.YYYY`;
- the host wrapper invokes the external Telegram sender after the Core outbox
  row is ready.

The operational report does not claim that data is final.

## Stage B Reconcile 06:00

The reconcile run is:

```bash
ops/bin/run-daily-coo-report.sh --reconcile --json
```

Rules:

- scheduled daily at `06:00 Asia/Yekaterinburg`;
- `business_date` is the previous local date;
- iiko sales are imported again;
- the latest closeout `sales_daily` payload is compared with the new payload,
  even if the overall outbox status is `pending` or `failed`;
- unchanged data records a successful reconciliation and creates no new
  message, but the wrapper still runs the sender with `--retry-failed` so failed
  recipients can recover;
- changed data creates one `sales_daily_correction` outbox row.

The correction title is:

```text
Dos Amigos — корректировка итогов DD.MM.YYYY
```

The correction contains only changed metrics with old and new values. Repeating
reconcile with the same changed values reuses the same idempotency key and does
not create another correction.

## Recipient-Level Delivery

Stage B adds `hermes_report_recipient_deliveries`.

The sender calculates an opaque `recipient_key` for each chat and registers
recipient keys through Core. The key is HMAC-SHA256 and must be exactly 64
lowercase hex characters. Core validates recipient keys and stores:

- `report_id + recipient_key` uniqueness;
- `pending`, `delivered`, or `failed`;
- attempts;
- `delivered_at`;
- `last_attempt_at`;
- safe redacted error text.

Core does not store raw Telegram IDs. On retry, the sender skips recipients
already marked `delivered` and sends only to recipients still pending or failed.
Once any recipient delivery row exists for a report, the report payload is
immutable. Later changed iiko data is handled by morning reconciliation through a
separate correction report, so all recipients of one `report_id` see the same
text.

`REPORT_RECIPIENT_KEY_SECRET` is mandatory in the Telegram sender environment
and must not be reused from `REPORT_OUTBOX_INTERNAL_TOKEN`. Generate it with,
for example:

```bash
openssl rand -hex 32
```

Use at least 32 characters. Rotating this secret changes recipient keys and
requires a separate migration/reconciliation of existing
`hermes_report_recipient_deliveries`; it is not a silent runtime change.

## Manual Commands

Dry-run:

```bash
ops/bin/run-daily-coo-report.sh --closeout --dry-run --json
ops/bin/run-daily-coo-report.sh --reconcile --dry-run --json
```

Dry-run performs iiko fetch and normalization read-only. It does not write sales
tables, `daily_coo_report_runs`, outbox rows, or Telegram messages.

Manual date:

```bash
ops/bin/run-daily-coo-report.sh --closeout --date YYYY-MM-DD --json
ops/bin/run-daily-coo-report.sh --reconcile --date YYYY-MM-DD --json
```

Retry failed Telegram recipients:

```bash
ops/bin/send-telegram-reports.sh --date YYYY-MM-DD --retry-failed --json
```

## Systemd Artifacts

Repository-only artifacts:

- `run-daily-coo-report.sh`
- `dos-amigos-daily-coo-closeout.service`
- `dos-amigos-daily-coo-closeout.timer`
- `dos-amigos-daily-coo-reconcile.service`
- `dos-amigos-daily-coo-reconcile.timer`

The services use `WorkingDirectory=/opt/dos-amigos-core` and call the host
wrapper. The wrapper executes:

```bash
docker compose \
  --project-name dos-amigos-core \
  --env-file /opt/dos-amigos-core/.env \
  --file /opt/dos-amigos-core/compose.yaml \
  exec -T core \
  python -m apps.core.app.cli.daily_coo_report ...
```

The timers use explicit `Asia/Yekaterinburg` `OnCalendar` values,
`Persistent=true`, and no `RandomizedDelaySec`.

Because persistent closeout and reconcile timers can catch up after downtime,
the host wrapper waits for the shared lock with
`DOS_AMIGOS_DAILY_COO_LOCK_WAIT_SECONDS` instead of treating lock contention as
success. If Core returns `already_running`, the wrapper retries the Core CLI a
bounded number of times before failing the unit without running the sender.

Failed systemd runs restart automatically with `Restart=on-failure` and
`RestartSec=60`, limited by `StartLimitIntervalSec=3600` and
`StartLimitBurst=5`. This covers temporary lock timeouts, Docker/Core/iiko
failures, and sender failures without looping forever. Alert outbox rows,
corrections, and recipient-level delivery rows remain idempotent across
restarts, so already delivered recipients are skipped on retry.

After the start limit is exhausted, inspect manually:

```bash
systemctl status dos-amigos-daily-coo-closeout.service
systemctl status dos-amigos-daily-coo-reconcile.service
journalctl -u dos-amigos-daily-coo-closeout.service
journalctl -u dos-amigos-daily-coo-reconcile.service
systemctl reset-failed dos-amigos-daily-coo-closeout.service
systemctl reset-failed dos-amigos-daily-coo-reconcile.service
```

## Recovery

iiko failure:

- no false report is created;
- the run is recorded as failed;
- a `sales_daily_alert` outbox row is created for safe technical notification
  when Core can create it;
- failed sync details stay redacted.

Telegram failure:

- report remains retryable;
- recipient rows already marked `delivered` are not sent again;
- rerun the sender with `--retry-failed`.

Correction failure:

- original delivered closeout report is not rewritten;
- correction outbox remains pending or failed for retry.

## Rollback

Deployment order:

1. copy the new unit files, but do not enable or start the new timers yet;
2. run manual closeout and reconcile dry-runs;
3. stop and disable `dos-amigos-sales-sync.timer`;
4. verify `dos-amigos-sales-sync.service` is not running;
5. run `systemctl daemon-reload`;
6. enable and start the new closeout/reconcile timers;
7. verify `systemctl list-timers` and the `Asia/Yekaterinburg` schedules.

Rollback:

1. disable `dos-amigos-daily-coo-closeout.timer` and
   `dos-amigos-daily-coo-reconcile.timer`;
2. enable and start `dos-amigos-sales-sync.timer` again;
3. verify its next scheduled run;
4. keep Stage A manual sender available because it still reads
   `hermes_report_outbox`.

Do not drop `hermes_report_recipient_deliveries` or `daily_coo_report_runs`
until pending and failed deliveries have been reviewed.

## Production Acceptance

- closeout chooses current `Asia/Yekaterinburg` date;
- reconcile chooses previous `Asia/Yekaterinburg` date;
- unchanged reconcile sends nothing;
- changed reconcile creates one correction;
- recipient retry sends only not-delivered recipients;
- `REPORT_RECIPIENT_KEY_SECRET` is present only in the sender environment;
- no logs contain Telegram IDs, `BOT_TOKEN`, `REPORT_OUTBOX_INTERNAL_TOKEN`,
  `DATABASE_URL`, or iiko credentials;
- systemd timers are reviewed before installation;
- no deployment is part of this repository change.
