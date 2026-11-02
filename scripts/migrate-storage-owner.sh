#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

for service in backend worker frontend caddy; do
  if docker compose ps --status running --services | grep -qx "$service"; then
    echo "Dừng $service trước khi chuyển ownership; script không tự dừng dịch vụ." >&2
    exit 1
  fi
done

docker compose --profile storage-migration run --rm --no-deps storage-owner --assert-idle --check
./scripts/backup.sh
backup="$(ls -1t backups/boq-backup-*.tar.gz | head -n 1)"
python3 - "$backup" <<'PY'
import sys, tarfile
with tarfile.open(sys.argv[1], "r:gz") as archive:
    names = set(archive.getnames())
if "data/database/boq.db" not in names or "data/jobs" not in names:
    raise SystemExit("Backup thiếu data/database/boq.db hoặc data/jobs; chưa đổi ownership.")
PY
docker compose --profile storage-migration run --rm --no-deps storage-owner --assert-idle --apply
docker compose --profile storage-migration run --rm --no-deps storage-owner --check
echo "Đã đổi ownership persistent storage sang 10001:10001. Các service vẫn đang dừng; chạy lại deploy khi sẵn sàng."
