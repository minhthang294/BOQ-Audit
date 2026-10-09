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
docker compose pull backend frontend chat

if docker compose ps --status running --services | grep -qx backend; then
  active_counts="$(docker compose exec -T backend python -c 'from sqlalchemy import func, select; from app.core.database import SessionLocal; from app.models.entities import AuditRun, Job, JobStatus; db = SessionLocal(); jobs = db.scalar(select(func.count()).select_from(Job).where(Job.status.in_((JobStatus.SUBMITTED, JobStatus.PROCESSING)))) or 0; runs = db.scalar(select(func.count()).select_from(AuditRun).where(AuditRun.status == "RUNNING")) or 0; db.close(); print(f"{jobs}:{runs}")')"
  if [[ "$active_counts" != 0:0 ]]; then
    echo "Từ chối deploy: có audit đang chờ/chạy ($active_counts); chờ xử lý xong rồi chạy lại." >&2
    exit 1
  fi
fi

rollback_ready=true
for service in backend frontend chat; do
  image_id="$(docker compose images --quiet "$service" | head -n 1)"
  if [[ -z "$image_id" ]]; then
    rollback_ready=false
    break
  fi
  docker image tag "$image_id" "boq-audit/$service:rollback"
done

health_check() {
  local host="${FRONTEND_URL#https://}"
  host="${host%%/*}"
  curl --resolve "${host}:443:127.0.0.1" --fail --silent --show-error --retry 10 --retry-delay 3 --retry-connrefused --max-time 15 "${FRONTEND_URL}/api/health" >/dev/null &&
    curl --resolve "${host}:443:127.0.0.1" --fail --silent --show-error --retry 10 --retry-delay 3 --retry-connrefused --max-time 15 "${FRONTEND_URL}/login" >/dev/null
}

if ! docker compose up -d --no-build --remove-orphans --wait --wait-timeout 240 || ! health_check; then
  echo "Release $IMAGE_TAG chưa đạt health check." >&2
  if [[ "$rollback_ready" == true ]]; then
    echo "Khôi phục image đang chạy trước deploy." >&2
    if ! IMAGE_REGISTRY=boq-audit IMAGE_TAG=rollback docker compose up -d --no-build --remove-orphans --wait --wait-timeout 240 || ! health_check; then
      docker compose logs --tail=100 backend frontend chat caddy >&2 || true
      echo "Rollback không đạt health check; cần kiểm tra VM thủ công." >&2
    fi
  else
    echo "Chưa có image đang chạy để tự rollback; cần kiểm tra VM thủ công." >&2
  fi
  exit 1
fi

echo "Đã deploy và health-check thành công: $IMAGE_TAG"
