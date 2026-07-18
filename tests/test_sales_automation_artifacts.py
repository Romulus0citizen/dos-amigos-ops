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
