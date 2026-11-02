#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

check_storage() {
  docker compose --profile storage-migration run --rm --no-deps storage-owner --assert-idle --check
}

if check_storage; then
  exit 0
else
  status=$?
  if [[ "$status" -ne 1 ]]; then
    exit "$status"
  fi
fi

docker compose stop caddy
if check_storage; then
  docker compose start caddy
  exit 0
else
  status=$?
  if [[ "$status" -ne 1 ]]; then
    docker compose start caddy
    exit "$status"
  fi
fi

docker compose stop frontend worker backend
bash scripts/migrate-storage-owner.sh
