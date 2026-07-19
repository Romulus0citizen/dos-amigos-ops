#!/usr/bin/env bash
set -Eeuo pipefail

CORE_DIR="${DOS_AMIGOS_CORE_DIR:-/opt/dos-amigos-core}"
LOCK_FILE="${DOS_AMIGOS_DAILY_COO_REPORT_LOCK_FILE:-/tmp/dos-amigos-daily-coo-report.lock}"
LOCK_WAIT_SECONDS="${DOS_AMIGOS_DAILY_COO_LOCK_WAIT_SECONDS:-1800}"
CORE_RETRY_ATTEMPTS="${DOS_AMIGOS_DAILY_COO_CORE_RETRY_ATTEMPTS:-10}"
CORE_RETRY_SLEEP_SECONDS="${DOS_AMIGOS_DAILY_COO_CORE_RETRY_SLEEP_SECONDS:-30}"

json_requested=false
dry_run=false
core_args=("$@")
for arg in "$@"; do
  case "${arg}" in
    --json)
      json_requested=true
      ;;
    --dry-run)
      dry_run=true
      ;;
  esac
done
if [[ "${json_requested}" == "false" ]]; then
  core_args+=("--json")
fi

exec 9>"${LOCK_FILE}"
if ! flock -w "${LOCK_WAIT_SECONDS}" 9; then
  printf 'daily COO report wrapper: lock timeout after %s seconds\n' "${LOCK_WAIT_SECONDS}" >&2
  exit 75
fi

core_output_file="$(mktemp)"
trap 'rm -f "${core_output_file}"' EXIT

core_exit=0
core_status=""
attempt=1
while true; do
  core_exit=0
  docker compose \
    --project-name dos-amigos-core \
    --env-file "${CORE_DIR}/.env" \
    --file "${CORE_DIR}/compose.yaml" \
    exec -T core \
    python -m apps.core.app.cli.daily_coo_report "${core_args[@]}" \
    >"${core_output_file}" || core_exit=$?

  cat "${core_output_file}"
  core_status="$(
    sed -nE 's/.*"status"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' "${core_output_file}" \
      | tail -n 1
  )"
  if [[ "${core_status}" != "already_running" ]]; then
    break
  fi
  if [[ "${attempt}" -ge "${CORE_RETRY_ATTEMPTS}" ]]; then
    printf 'daily COO report wrapper: Core remained already_running after %s attempts\n' "${attempt}" >&2
    exit 75
  fi
  sleep "${CORE_RETRY_SLEEP_SECONDS}"
  attempt=$((attempt + 1))
done

business_date="$(
  sed -nE 's/.*"business_date"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' "${core_output_file}" \
    | tail -n 1
)"
if [[ -z "${business_date}" ]]; then
  if [[ "${core_exit}" -ne 0 ]]; then
    exit "${core_exit}"
  fi
  printf 'daily COO report wrapper: business_date missing from Core output\n' >&2
  exit 1
fi

sender_args=("--date" "${business_date}" "--retry-failed" "--json")
if [[ "${dry_run}" == "true" ]]; then
  sender_args+=("--dry-run")
fi

sender_exit=0
"${CORE_DIR}/ops/bin/send-telegram-reports.sh" "${sender_args[@]}" || sender_exit=$?

if [[ "${core_exit}" -ne 0 ]]; then
  exit "${core_exit}"
fi
exit "${sender_exit}"
