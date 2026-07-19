from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _write_fake_docker(
    bin_dir: Path,
    log_file: Path,
    *,
    payloads: list[str],
    exit_codes: list[int] | None = None,
) -> None:
    docker = bin_dir / "docker"
    payload_file = bin_dir / "docker-payloads"
    exit_code_file = bin_dir / "docker-exit-codes"
    count_file = bin_dir / "docker-count"
    payload_file.write_text("\n".join(payloads) + "\n", encoding="utf-8")
    exit_code_file.write_text(
        "\n".join(str(code) for code in (exit_codes or [0] * len(payloads))) + "\n",
        encoding="utf-8",
    )
    docker.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -Eeuo pipefail",
                f"printf 'docker:%s\\n' \"$*\" >> {log_file}",
                f"count_file='{count_file}'",
                "count=0",
                '[[ -f "${count_file}" ]] && count="$(cat "${count_file}")"',
                "count=$((count + 1))",
                'printf \'%s\\n\' "${count}" > "${count_file}"',
                f'payload="$(sed -n "${{count}}p" \'{payload_file}\')"',
                f'exit_code="$(sed -n "${{count}}p" \'{exit_code_file}\')"',
                f"last_payload=\"$(tail -n 1 '{payload_file}')\"",
                f"last_exit_code=\"$(tail -n 1 '{exit_code_file}')\"",
                "printf '%s\\n' \"${payload:-${last_payload}}\"",
                'exit "${exit_code:-${last_exit_code}}"',
            ]
        ),
        encoding="utf-8",
    )
    docker.chmod(0o755)


def _write_fake_sender(core_dir: Path, log_file: Path, *, exit_code: int = 0) -> None:
    sender = core_dir / "ops/bin/send-telegram-reports.sh"
    sender.parent.mkdir(parents=True)
    sender.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -Eeuo pipefail",
                f"printf 'sender:%s\\n' \"$*\" >> {log_file}",
                f"exit {exit_code}",
            ]
        ),
        encoding="utf-8",
    )
    sender.chmod(0o755)


def _write_fake_flock(bin_dir: Path, log_file: Path, *, mode: str = "success") -> None:
    flock = bin_dir / "flock"
    state_dir = bin_dir / "fake-flock-held"
    flock.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -Eeuo pipefail",
                f"printf 'flock:%s\\n' \"$*\" >> {log_file}",
                f"mode='{mode}'",
                "if [[ \"${mode}\" == 'timeout' ]]; then exit 1; fi",
                "if [[ \"${mode}\" == 'wait' ]]; then",
                f"  if mkdir '{state_dir}' 2>/dev/null; then",
                '    sleep "${FAKE_FLOCK_HOLD_SECONDS:-0.2}"',
                f"    rmdir '{state_dir}'",
                "    exit 0",
                "  fi",
                f"  while [[ -d '{state_dir}' ]]; do sleep 0.02; done",
                "fi",
                "exit 0",
            ]
        ),
        encoding="utf-8",
    )
    flock.chmod(0o755)


def _prepare_wrapper(
    tmp_path: Path,
    *,
    payloads: list[str],
    docker_exit_codes: list[int] | None = None,
    flock_mode: str = "success",
):
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / ".env").write_text("APP_ENV=test\n", encoding="utf-8")
    (core_dir / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    log_file = tmp_path / "calls.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_docker(
        bin_dir,
        log_file,
        payloads=payloads,
        exit_codes=docker_exit_codes,
    )
    _write_fake_flock(bin_dir, log_file, mode=flock_mode)
    _write_fake_sender(core_dir, log_file)
    env = {
        **os.environ,
        "DOS_AMIGOS_CORE_DIR": str(core_dir),
        "DOS_AMIGOS_DAILY_COO_REPORT_LOCK_FILE": str(tmp_path / "lock"),
        "DOS_AMIGOS_DAILY_COO_CORE_RETRY_SLEEP_SECONDS": "0",
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }
    return log_file, env


def _run_wrapper(
    tmp_path: Path,
    *,
    payload: str,
    args: list[str],
    docker_exit: int = 0,
    flock_mode: str = "success",
    extra_env: dict[str, str] | None = None,
):
    log_file, env = _prepare_wrapper(
        tmp_path,
        payloads=[payload],
        docker_exit_codes=[docker_exit],
        flock_mode=flock_mode,
    )
    env.update(extra_env or {})
    completed = subprocess.run(
        [str(ROOT / "ops/bin/run-daily-coo-report.sh"), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed, log_file.read_text(encoding="utf-8").splitlines()


def test_wrapper_runs_core_cli_before_sender_for_closeout(tmp_path: Path) -> None:
    payload = (
        '{"status":"outbox_ready","mode":"closeout","business_date":"2026-07-19",'
        '"outbox_ids":["report-1"],"dry_run":false,"error_code":null}'
    )

    completed, calls = _run_wrapper(tmp_path, payload=payload, args=["--closeout", "--json"])

    assert completed.returncode == 0
    assert calls == [
        "flock:-w 1800 9",
        (
            "docker:compose --project-name dos-amigos-core "
            f"--env-file {tmp_path}/core/.env --file {tmp_path}/core/compose.yaml "
            "exec -T core python -m apps.core.app.cli.daily_coo_report --closeout --json"
        ),
        "sender:--date 2026-07-19 --retry-failed --json",
    ]


def test_wrapper_runs_sender_for_unchanged_reconcile_retry_failed(tmp_path: Path) -> None:
    payload = (
        '{"status":"reconciled","mode":"reconcile","business_date":"2026-07-18",'
        '"outbox_ids":[],"dry_run":false,"error_code":null}'
    )

    completed, calls = _run_wrapper(tmp_path, payload=payload, args=["--reconcile", "--json"])

    assert completed.returncode == 0
    assert calls[-1] == "sender:--date 2026-07-18 --retry-failed --json"


def test_wrapper_runs_dry_run_sender_only_as_dry_run(tmp_path: Path) -> None:
    payload = (
        '{"status":"dry_run_ready","mode":"closeout","business_date":"2026-07-19",'
        '"outbox_ids":[],"dry_run":true,"error_code":null}'
    )

    completed, calls = _run_wrapper(
        tmp_path,
        payload=payload,
        args=["--closeout", "--dry-run", "--json"],
    )

    assert completed.returncode == 0
    assert calls[-1] == "sender:--date 2026-07-19 --retry-failed --json --dry-run"


def test_wrapper_sends_alert_even_when_core_returns_failed_with_outbox(tmp_path: Path) -> None:
    payload = (
        '{"status":"failed","mode":"closeout","business_date":"2026-07-19",'
        '"outbox_ids":["alert-1"],"dry_run":false,"error_code":"iiko_unavailable"}'
    )

    completed, calls = _run_wrapper(
        tmp_path,
        payload=payload,
        args=["--closeout", "--json"],
        docker_exit=1,
    )

    assert completed.returncode == 1
    assert calls[-1] == "sender:--date 2026-07-19 --retry-failed --json"


def test_wrapper_lock_timeout_is_temporary_failure_without_sender(tmp_path: Path) -> None:
    payload = (
        '{"status":"outbox_ready","mode":"closeout","business_date":"2026-07-19",'
        '"outbox_ids":["report-1"],"dry_run":false,"error_code":null}'
    )

    completed, calls = _run_wrapper(
        tmp_path,
        payload=payload,
        args=["--closeout", "--json"],
        flock_mode="timeout",
        extra_env={"DOS_AMIGOS_DAILY_COO_LOCK_WAIT_SECONDS": "1"},
    )

    assert completed.returncode != 0
    assert calls == ["flock:-w 1 9"]
    assert "already_running" not in completed.stdout


def test_wrapper_waits_for_lock_and_second_run_executes_after_release(tmp_path: Path) -> None:
    payload = (
        '{"status":"outbox_ready","mode":"closeout","business_date":"2026-07-19",'
        '"outbox_ids":["report-1"],"dry_run":false,"error_code":null}'
    )
    log_file, env = _prepare_wrapper(
        tmp_path,
        payloads=[payload, payload],
        flock_mode="wait",
    )
    command = [str(ROOT / "ops/bin/run-daily-coo-report.sh"), "--closeout", "--json"]

    first = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    time.sleep(0.05)
    second = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    first_stdout, first_stderr = first.communicate(timeout=5)
    second_stdout, second_stderr = second.communicate(timeout=5)

    assert first.returncode == 0, first_stderr
    assert second.returncode == 0, second_stderr
    assert "already_running" not in first_stdout
    assert "already_running" not in second_stdout
    calls = log_file.read_text(encoding="utf-8").splitlines()
    assert sum(call.startswith("docker:") for call in calls) == 2
    assert sum(call.startswith("sender:") for call in calls) == 2


def test_wrapper_retries_core_already_running_before_sender(tmp_path: Path) -> None:
    already_running = (
        '{"status":"already_running","mode":"closeout","business_date":"2026-07-19",'
        '"outbox_ids":[],"dry_run":false,"error_code":null}'
    )
    ready = (
        '{"status":"outbox_ready","mode":"closeout","business_date":"2026-07-19",'
        '"outbox_ids":["report-1"],"dry_run":false,"error_code":null}'
    )
    log_file, env = _prepare_wrapper(
        tmp_path,
        payloads=[already_running, ready],
        flock_mode="success",
    )

    completed = subprocess.run(
        [str(ROOT / "ops/bin/run-daily-coo-report.sh"), "--closeout", "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0
    calls = log_file.read_text(encoding="utf-8").splitlines()
    assert sum(call.startswith("docker:") for call in calls) == 2
    assert calls[-1] == "sender:--date 2026-07-19 --retry-failed --json"


def test_wrapper_does_not_run_sender_when_core_stays_already_running(tmp_path: Path) -> None:
    already_running = (
        '{"status":"already_running","mode":"closeout","business_date":"2026-07-19",'
        '"outbox_ids":[],"dry_run":false,"error_code":null}'
    )
    log_file, env = _prepare_wrapper(
        tmp_path,
        payloads=[already_running, already_running],
        flock_mode="success",
    )
    env["DOS_AMIGOS_DAILY_COO_CORE_RETRY_ATTEMPTS"] = "2"

    completed = subprocess.run(
        [str(ROOT / "ops/bin/run-daily-coo-report.sh"), "--closeout", "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode != 0
    calls = log_file.read_text(encoding="utf-8").splitlines()
    assert sum(call.startswith("docker:") for call in calls) == 2
    assert not any(call.startswith("sender:") for call in calls)
