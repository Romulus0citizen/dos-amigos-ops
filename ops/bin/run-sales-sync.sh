#!/usr/bin/env bash
set -Eeuo pipefail

LOCK_FILE="${DOS_AMIGOS_SALES_SYNC_LOCK_FILE:-/tmp/dos-amigos-sales-sync.lock}"
RELEASE_DIR="${DOS_AMIGOS_RELEASE_DIR:-/opt/dos-amigos-core}"

exec 9>"${LOCK_FILE}"
flock -n 9

if [[ ! -d "${RELEASE_DIR}" ]]; then
  echo "active release directory not found" >&2
  exit 1
fi

cd "${RELEASE_DIR}"

docker compose ps --status running core >/dev/null
docker compose exec -T core \
  python -m apps.core.app.cli.iiko_sales_automation \
  --run-due \
  --json
