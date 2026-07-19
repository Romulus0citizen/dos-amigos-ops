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
