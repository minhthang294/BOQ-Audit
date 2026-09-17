#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Cách dùng: $0 backups/boq-backup-YYYY-MM-DD-HHMMSS.tar.gz" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_PATH="${DATA_PATH:-${ROOT_DIR}/data}"
ARCHIVE="$(realpath "$1")"

if [[ ! -f "${ARCHIVE}" ]] || ! tar -tzf "${ARCHIVE}" | awk '{ if ($0 ~ /^\// || $0 ~ /(^|\/)\.\.($|\/)/) exit 1 }'; then
  echo "Backup không hợp lệ hoặc chứa đường dẫn không an toàn." >&2
  exit 1
fi

snapshot="${DATA_PATH}.before-restore-$(date +%Y%m%d%H%M%S)"
if [[ -d "${DATA_PATH}" ]]; then
  mv "${DATA_PATH}" "${snapshot}"
fi
mkdir -p "$(dirname "${DATA_PATH}")"
tar -C "$(dirname "${DATA_PATH}")" -xzf "${ARCHIVE}"
echo "Đã phục hồi dữ liệu. Bản dữ liệu trước restore: ${snapshot}"

