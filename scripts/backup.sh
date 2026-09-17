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

tar -C "$(dirname "${DATA_PATH}")" -czf "${archive}" "$(basename "${DATA_PATH}")/database" "$(basename "${DATA_PATH}")/jobs"
echo "Đã tạo backup: ${archive}"

