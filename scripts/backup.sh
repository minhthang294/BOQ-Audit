#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_PATH="${DATA_PATH:-${ROOT_DIR}/data}"
BACKUP_PATH="${BACKUP_PATH:-${ROOT_DIR}/backups}"

if [[ ! -d "${DATA_PATH}" ]]; then
  echo "Không tìm thấy thư mục dữ liệu: ${DATA_PATH}" >&2
  exit 1
fi

mkdir -p "${BACKUP_PATH}"
stamp="$(date +%Y-%m-%d-%H%M%S)"
archive="${BACKUP_PATH}/boq-backup-${stamp}.tar.gz"
stage="$(mktemp -d)"
trap 'rm -rf "${stage}"' EXIT
mkdir -p "${stage}/data/database" "${stage}/data/jobs"

database="${DATA_PATH}/database/boq.db"
if [[ -f "${database}" ]]; then
  python3 - "${database}" "${stage}/data/database/boq.db" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
destination = sqlite3.connect(sys.argv[2])
try:
    source.backup(destination)
finally:
    destination.close()
    source.close()
PY
fi

if [[ -d "${DATA_PATH}/jobs" ]]; then
  cp -a "${DATA_PATH}/jobs/." "${stage}/data/jobs/"
fi

tar -C "${stage}" -czf "${archive}" data/database data/jobs
echo "Đã tạo backup: ${archive}"
