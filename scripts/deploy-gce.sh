#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env || ! -f .deploy.env ]]; then
  echo "Cần có .env production và .deploy.env do workflow tạo." >&2
  exit 1
fi

mapfile -t deploy_config < .deploy.env
if [[ ${#deploy_config[@]} -ne 2 || "${deploy_config[0]}" != IMAGE_REGISTRY=* || "${deploy_config[1]}" != IMAGE_TAG=* ]]; then
  echo ".deploy.env không đúng định dạng; cần IMAGE_REGISTRY và IMAGE_TAG." >&2
  exit 1
fi
IMAGE_REGISTRY="${deploy_config[0]#IMAGE_REGISTRY=}"
IMAGE_TAG="${deploy_config[1]#IMAGE_TAG=}"
[[ "$IMAGE_REGISTRY" =~ ^[a-z0-9-]+-docker[.]pkg[.]dev/[a-z][a-z0-9-]{4,28}/[a-z][a-z0-9-]{2,62}$ ]] || { echo "IMAGE_REGISTRY không hợp lệ." >&2; exit 1; }
[[ "$IMAGE_TAG" =~ ^[0-9a-f]{40}-[0-9]{1,20}-[0-9]{1,3}$ ]] || { echo "IMAGE_TAG phải có dạng SHA-run_id-run_attempt." >&2; exit 1; }
export IMAGE_REGISTRY IMAGE_TAG

FRONTEND_URL="$(sed -n 's/^FRONTEND_URL=//p' .env | tail -n 1)"
[[ "$FRONTEND_URL" == https://* ]] || { echo "FRONTEND_URL trong .env phải dùng HTTPS." >&2; exit 1; }
FRONTEND_URL="${FRONTEND_URL%/}"
docker compose config --quiet
docker compose pull backend frontend

if docker compose ps --status running --services | grep -qx backend; then
  active_counts="$(docker compose exec -T backend python -c 'from sqlalchemy import inspect, text; from app.core.database import engine; connection = engine.connect(); jobs = connection.scalar(text("SELECT COUNT(*) FROM jobs WHERE status IN (\x27SUBMITTED\x27, \x27PROCESSING\x27)")) or 0; runs = connection.scalar(text("SELECT COUNT(*) FROM audit_runs WHERE status = \x27RUNNING\x27")) or 0 if inspect(engine).has_table("audit_runs") else 0; connection.close(); print(f"{jobs}:{runs}")')"
  if [[ "$active_counts" != 0:0 ]]; then
    echo "Từ chối deploy: có audit đang chờ/chạy ($active_counts); chờ xử lý xong rồi chạy lại." >&2
    exit 1
  fi
fi

rollback_ready=true
rollback_services=(backend frontend)
rollback_chat_ready=false
if grep -Eq '^CHAT_GATEWAY_TOKEN=.{32,}$' .env && docker compose --profile chat ps --status running --services | grep -qx chat; then
  rollback_services+=(chat)
fi
for service in "${rollback_services[@]}"; do
  image_id="$(docker compose images --quiet "$service" | head -n 1)"
  if [[ -z "$image_id" ]]; then
    rollback_ready=false
    break
  fi
  docker image tag "$image_id" "boq-audit/$service:rollback"
  [[ "$service" != chat ]] || rollback_chat_ready=true
done

compose_profile_args=()
if grep -Eq '^CHAT_GATEWAY_TOKEN=.{32,}$' .env; then
  compose_profile_args=(--profile chat)
  docker compose --profile chat pull chat
  docker compose --profile chat-setup run --rm chat-auth-init
elif docker compose --profile chat ps --status running --services | grep -qx chat; then
  docker compose --profile chat stop chat
fi

health_check() {
  local host="${FRONTEND_URL#https://}"
  host="${host%%/*}"
  curl --resolve "${host}:443:127.0.0.1" --fail --silent --show-error --retry 10 --retry-delay 3 --retry-connrefused --max-time 15 "${FRONTEND_URL}/api/health" >/dev/null &&
    curl --resolve "${host}:443:127.0.0.1" --fail --silent --show-error --retry 10 --retry-delay 3 --retry-connrefused --max-time 15 "${FRONTEND_URL}/login" >/dev/null
}

if ! docker compose "${compose_profile_args[@]}" up -d --no-build --remove-orphans --wait --wait-timeout 240 || ! health_check; then
  echo "Release $IMAGE_TAG chưa đạt health check." >&2
  if [[ "$rollback_ready" == true ]]; then
    echo "Khôi phục image đang chạy trước deploy." >&2
    rollback_profile_args=()
    if [[ "$rollback_chat_ready" == true ]]; then
      rollback_profile_args=(--profile chat)
    elif [[ "${#compose_profile_args[@]}" -gt 0 ]]; then
      docker compose --profile chat stop chat || true
    fi
    if ! IMAGE_REGISTRY=boq-audit IMAGE_TAG=rollback docker compose "${rollback_profile_args[@]}" up -d --no-build --remove-orphans --wait --wait-timeout 240 || ! health_check; then
      docker compose logs --tail=100 backend frontend chat caddy >&2 || true
      echo "Rollback không đạt health check; cần kiểm tra VM thủ công." >&2
    fi
  else
    echo "Chưa có image đang chạy để tự rollback; cần kiểm tra VM thủ công." >&2
  fi
  exit 1
fi

echo "Đã deploy và health-check thành công: $IMAGE_TAG"
