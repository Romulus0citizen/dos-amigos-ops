from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_systemd_artifacts_do_not_contain_secrets_or_production_ip() -> None:
    paths = [
        ROOT / "ops/systemd/dos-amigos-sales-sync.service",
        ROOT / "ops/systemd/dos-amigos-sales-sync.timer",
        ROOT / "ops/bin/run-sales-sync.sh",
    ]
    forbidden = ["password", "token", "DATABASE_URL", "8081", "8082", "8083", "8084"]

    for path in paths:
        text = path.read_text(encoding="utf-8")
        rendered = text.lower()
        assert "production" not in rendered
        assert not any(item.lower() in rendered for item in forbidden)


def test_sales_sync_script_uses_strict_mode_and_flock() -> None:
    script = (ROOT / "ops/bin/run-sales-sync.sh").read_text(encoding="utf-8")

    assert "set -Eeuo pipefail" in script
    assert "flock -n" in script
    assert "iiko_sales_automation" in script
    assert ".env" not in script


def test_telegram_report_script_uses_bot_environment_defaults_and_flock() -> None:
    script = (ROOT / "ops/bin/send-telegram-reports.sh").read_text(encoding="utf-8")

    assert "set -Eeuo pipefail" in script
    assert 'BOT_DIR="${DOS_AMIGOS_BOT_DIR:-/opt/hermes-bots/dos-amigos}"' in script
    assert 'PYTHON_BIN="${DOS_AMIGOS_OPS_PYTHON:-${BOT_DIR}/.venv/bin/python}"' in script
    assert 'BOT_ENV_FILE="${BOT_ENV_FILE:-${BOT_DIR}/.env}"' in script
    assert (
        'LOCK_FILE="${DOS_AMIGOS_TELEGRAM_REPORT_LOCK_FILE:-/tmp/dos-amigos-telegram-report.lock}"'
        in script
    )
    assert "flock -n" in script
    assert "cat " not in script


def test_daily_coo_report_systemd_units_use_required_schedule_and_paths() -> None:
    closeout_service = (ROOT / "ops/systemd/dos-amigos-daily-coo-closeout.service").read_text(
        encoding="utf-8"
    )
    closeout_timer = (ROOT / "ops/systemd/dos-amigos-daily-coo-closeout.timer").read_text(
        encoding="utf-8"
    )
    reconcile_service = (ROOT / "ops/systemd/dos-amigos-daily-coo-reconcile.service").read_text(
        encoding="utf-8"
    )
    reconcile_timer = (ROOT / "ops/systemd/dos-amigos-daily-coo-reconcile.timer").read_text(
        encoding="utf-8"
    )

    assert (
        "ExecStart=/opt/dos-amigos-core/ops/bin/run-daily-coo-report.sh --closeout --json"
        in closeout_service
    )
    assert (
        "ExecStart=/opt/dos-amigos-core/ops/bin/run-daily-coo-report.sh --reconcile --json"
        in reconcile_service
    )
    assert "WorkingDirectory=/opt/dos-amigos-core" in closeout_service
    assert "WorkingDirectory=/opt/dos-amigos-core" in reconcile_service
    assert "/opt/dos-amigos-ops/current" not in closeout_service
    assert "/opt/dos-amigos-ops/current" not in reconcile_service
    assert ".venv312" not in closeout_service
    assert ".venv312" not in reconcile_service
    assert "python -m apps.core.app.cli.daily_coo_report" not in closeout_service
    assert "python -m apps.core.app.cli.daily_coo_report" not in reconcile_service
    for service in (closeout_service, reconcile_service):
        assert "Restart=on-failure" in service
        assert "RestartSec=60" in service
        assert "TimeoutStartSec=7200" in service
        assert "StartLimitIntervalSec=3600" in service
        assert "StartLimitBurst=5" in service
    assert "OnCalendar=*-*-* 23:30:00 Asia/Yekaterinburg" in closeout_timer
    assert "OnCalendar=*-*-* 06:00:00 Asia/Yekaterinburg" in reconcile_timer
    assert "Persistent=true" in closeout_timer
    assert "Persistent=true" in reconcile_timer
    assert "RandomizedDelaySec" not in closeout_timer
    assert "RandomizedDelaySec" not in reconcile_timer


def test_daily_coo_report_wrapper_uses_docker_core_then_host_sender() -> None:
    script = (ROOT / "ops/bin/run-daily-coo-report.sh").read_text(encoding="utf-8")

    assert "set -Eeuo pipefail" in script
    assert 'CORE_DIR="${DOS_AMIGOS_CORE_DIR:-/opt/dos-amigos-core}"' in script
    assert "flock -w" in script
    assert "DOS_AMIGOS_DAILY_COO_LOCK_WAIT_SECONDS" in script
    assert "DOS_AMIGOS_DAILY_COO_CORE_RETRY_ATTEMPTS" in script
    assert "DOS_AMIGOS_DAILY_COO_CORE_RETRY_SLEEP_SECONDS" in script
    assert "already_running" in script
    assert "--project-name dos-amigos-core" in script
    assert '--env-file "${CORE_DIR}/.env"' in script
    assert '--file "${CORE_DIR}/compose.yaml"' in script
    assert "exec -T core" in script
    assert "python -m apps.core.app.cli.daily_coo_report" in script
    assert "ops/bin/send-telegram-reports.sh" in script
    assert "--retry-failed" in script
    assert "BOT_TOKEN" not in script
    assert "ALLOWED_IDS" not in script


def test_daily_coo_report_core_cli_does_not_call_host_sender() -> None:
    cli = (ROOT / "apps/core/app/cli/daily_coo_report.py").read_text(encoding="utf-8")

    assert "SubprocessDailyCooReportDeliveryRunner" not in cli
    assert "send-telegram-reports.sh" not in cli
    assert "BOT_TOKEN" not in cli
    assert "ALLOWED_IDS" not in cli
    assert "/opt/hermes-bots" not in cli


def test_daily_coo_report_migration_places_recipient_fk_on_delivery_table() -> None:
    migration = (ROOT / "migrations/versions/20260719_0005_daily_coo_report_stage_b.py").read_text(
        encoding="utf-8"
    )
    recipient_block = migration.split('"hermes_report_recipient_deliveries"', 1)[1].split(
        'op.create_index(\n        "ix_hermes_report_recipient_delivery_report"',
        1,
    )[0]
    run_block = migration.split('"daily_coo_report_runs"', 1)[1].split(
        'op.create_index(\n        "ix_daily_coo_report_runs_mode_started"',
        1,
    )[0]

    assert 'sa.Column("recipient_key", sa.String(length=64), nullable=False)' in recipient_block
    assert "sa.ForeignKeyConstraint" in recipient_block
    assert '["hermes_report_outbox.id"]' in recipient_block
    assert 'ondelete="CASCADE"' in recipient_block
    assert "sa.ForeignKeyConstraint" not in run_block


def test_daily_coo_report_docs_disable_old_timer_before_enabling_new_timers() -> None:
    docs = (ROOT / "docs/s1-6-2-daily-coo-report.md").read_text(encoding="utf-8")

    copy_units = docs.index("copy the new unit files")
    dry_runs = docs.index("run manual closeout and reconcile dry-runs")
    disable_old = docs.index("stop and disable `dos-amigos-sales-sync.timer`")
    verify_old_service = docs.index("verify `dos-amigos-sales-sync.service` is not running")
    enable_new = docs.index("enable and start the new closeout/reconcile timers")

    assert copy_units < dry_runs < disable_old < verify_old_service < enable_new
    assert "enable and start `dos-amigos-sales-sync.timer` again" in docs
    assert "verify its next scheduled run" in docs
