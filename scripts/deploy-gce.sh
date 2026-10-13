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
first_cutover=false
if ! docker compose ps --status running --services | grep -qx worker; then
  first_cutover=true
  if docker compose ps --status running --services | grep -qx backend; then
    echo "First cutover cần dừng backend sau khi chờ audit hoàn tất và backup ghép DB/jobs." >&2
    exit 1
  fi
  cutover_compose="$ROOT_DIR/.cutover/docker-compose.yml"
  cutover_backup="$ROOT_DIR/.cutover/backup.tar.gz"
  if [[ ! -f "$cutover_compose" || ! -f "$cutover_backup" ]] || grep -Eq '^  worker:' "$cutover_compose" || ! tar -tzf "$cutover_backup" | awk '$0 == "data/database/boq.db" { db = 1 } $0 == "data/jobs/" { jobs = 1 } END { exit !(db && jobs) }'; then
    echo "First cutover cần .cutover/docker-compose.yml cũ và backup.tar.gz chứa DB/jobs; chưa thay container." >&2
    exit 1
  fi
fi
if [[ "$first_cutover" == false ]]; then
  rollback_services+=(worker)
fi
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
if [[ "$first_cutover" == true && "$rollback_ready" == false ]]; then
  echo "First cutover thiếu image cũ để rollback; chưa thay container." >&2
  exit 1
fi
docker compose pull worker

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

if [[ "$first_cutover" == true ]]; then
  if ! docker compose stop caddy; then
    echo "Không thể đóng ingress trước cutover; chưa thay container." >&2
    exit 1
  fi
  local_ready=false
  if docker compose up -d --no-build --wait --wait-timeout 240 backend worker frontend; then
    for _ in {1..10}; do
      if docker compose exec -T frontend node -e "fetch('http://127.0.0.1:3000/login').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"; then
        local_ready=true
        break
      fi
      sleep 2
    done
  fi
  if [[ "$local_ready" == true ]]; then
    if ! docker compose "${compose_profile_args[@]}" up -d --no-build --remove-orphans --wait --wait-timeout 240 || ! health_check; then
      docker compose stop caddy || true
      echo "Public health fail sau khi mở Caddy; ingress đã dừng. Giữ nguyên DB/jobs để đối soát thủ công, không tự restore backup." >&2
      exit 1
    fi
    echo "Đã deploy và health-check thành công: $IMAGE_TAG"
    exit 0
  fi
fi

if [[ "$first_cutover" == true ]] || ! docker compose "${compose_profile_args[@]}" up -d --no-build --remove-orphans --wait --wait-timeout 240 || ! health_check; then
  echo "Release $IMAGE_TAG chưa đạt health check." >&2
  if [[ "$rollback_ready" == true ]]; then
    echo "Khôi phục image đang chạy trước deploy." >&2
    rollback_profile_args=()
    if [[ "$rollback_chat_ready" == true ]]; then
      rollback_profile_args=(--profile chat)
    elif [[ "${#compose_profile_args[@]}" -gt 0 ]]; then
      docker compose --profile chat stop chat || true
    fi
    if [[ "$first_cutover" == true ]]; then
      docker compose stop worker backend frontend || true
      if ! ./scripts/restore.sh "$cutover_backup" || ! cp "$cutover_compose" docker-compose.yml; then
        echo "Rollback dữ liệu/Compose lỗi; giữ các service đã dừng để phục hồi thủ công." >&2
        exit 1
      fi
    fi
    if ! IMAGE_REGISTRY=boq-audit IMAGE_TAG=rollback docker compose "${rollback_profile_args[@]}" up -d --no-build --remove-orphans --wait --wait-timeout 240 || ! health_check; then
      docker compose logs --tail=100 backend frontend worker chat caddy >&2 || true
      echo "Rollback không đạt health check; cần kiểm tra VM thủ công." >&2
    fi
  else
    echo "Chưa có image đang chạy để tự rollback; cần kiểm tra VM thủ công." >&2
  fi
  exit 1
fi

echo "Đã deploy và health-check thành công: $IMAGE_TAG"
