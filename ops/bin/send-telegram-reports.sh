#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPS_DIR="${DOS_AMIGOS_OPS_DIR:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
BOT_DIR="${DOS_AMIGOS_BOT_DIR:-/opt/hermes-bots/dos-amigos}"
PYTHON_BIN="${DOS_AMIGOS_OPS_PYTHON:-${BOT_DIR}/.venv/bin/python}"
BOT_ENV_FILE="${BOT_ENV_FILE:-${BOT_DIR}/.env}"
LOCK_FILE="${DOS_AMIGOS_TELEGRAM_REPORT_LOCK_FILE:-/tmp/dos-amigos-telegram-report.lock}"

export BOT_ENV_FILE

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  printf 'status=already_running considered=0 delivered=0 failed=0 messages_sent=0 dry_run=false\n'
  exit 0
fi

cd "${OPS_DIR}"
exec "${PYTHON_BIN}" -m ops.telegram.report_sender --once "$@"
