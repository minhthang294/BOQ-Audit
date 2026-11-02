#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mapfile -t deploy_config < .deploy.env
if [[ ${#deploy_config[@]} -lt 2 || ${#deploy_config[@]} -gt 3 || "${deploy_config[0]}" != IMAGE_REGISTRY=* || "${deploy_config[1]}" != IMAGE_TAG=* || ( ${#deploy_config[@]} -eq 3 && "${deploy_config[2]}" != BOQ_AUDIT_SKILL_HOST_PATH=* ) ]]; then
  echo ".deploy.env không đúng định dạng." >&2
  exit 1
fi
IMAGE_REGISTRY="${deploy_config[0]#IMAGE_REGISTRY=}"
IMAGE_TAG="${deploy_config[1]#IMAGE_TAG=}"
[[ "$IMAGE_REGISTRY" =~ ^[a-z0-9-]+-docker[.]pkg[.]dev/[a-z][a-z0-9-]{4,28}/[a-z][a-z0-9-]{2,62}$ ]] || { echo "IMAGE_REGISTRY không hợp lệ." >&2; exit 1; }
[[ "$IMAGE_TAG" =~ ^[0-9a-f]{40}-[0-9]{1,20}-[0-9]{1,3}$ ]] || { echo "IMAGE_TAG không hợp lệ." >&2; exit 1; }
export IMAGE_REGISTRY IMAGE_TAG
docker compose pull backend

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
