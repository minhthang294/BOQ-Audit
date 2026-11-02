#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT

make_case() {
  local name="$1"
  local dir="$test_root/$name"
  mkdir -p "$dir/scripts" "$dir/bin"
  printf 'IMAGE_REGISTRY=asia-southeast1-docker.pkg.dev/test-project/boq-audit\nIMAGE_TAG=0123456789abcdef0123456789abcdef01234567-123456-1\n' > "$dir/.deploy.env"
  cp "$repo_root/scripts/prepare_storage_migration.sh" "$dir/scripts/prepare_storage_migration.sh"
  cat > "$dir/scripts/migrate-storage-owner.sh" <<'FAKE_MIGRATE'
#!/usr/bin/env bash
touch "$MIGRATION_MARKER"
FAKE_MIGRATE
  cat > "$dir/bin/docker" <<'FAKE_DOCKER'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$DOCKER_LOG"
case "$*" in
  "compose --profile storage-migration run --rm --no-deps storage-owner --assert-idle --check")
    count=0
    [[ ! -f "$CHECK_COUNT" ]] || count="$(cat "$CHECK_COUNT")"
    count=$((count + 1))
    printf '%s' "$count" > "$CHECK_COUNT"
    case "$CHECK_MODE:$count" in
      clean:*) exit 0 ;;
      active-second:2) exit 2 ;;
      *) exit 1 ;;
    esac
    ;;
  "compose pull backend"|"compose stop caddy"|"compose start caddy"|"compose stop frontend worker backend") ;;
  *) echo "Unexpected docker command: $*" >&2; exit 2 ;;
esac
FAKE_DOCKER
  chmod +x "$dir/bin/docker" "$dir/scripts/migrate-storage-owner.sh"
  printf '%s\n' "$dir"
}

clean_case="$(make_case clean)"
(cd "$clean_case" && PATH="$clean_case/bin:$PATH" CHECK_MODE=clean CHECK_COUNT="$clean_case/count" DOCKER_LOG="$clean_case/docker.log" MIGRATION_MARKER="$clean_case/migrated" bash scripts/prepare_storage_migration.sh)
! grep -Fq 'compose stop ' "$clean_case/docker.log"
[[ ! -e "$clean_case/migrated" ]]

migrate_case="$(make_case migrate)"
(cd "$migrate_case" && PATH="$migrate_case/bin:$PATH" CHECK_MODE=mismatch CHECK_COUNT="$migrate_case/count" DOCKER_LOG="$migrate_case/docker.log" MIGRATION_MARKER="$migrate_case/migrated" bash scripts/prepare_storage_migration.sh)
grep -Fxq 'compose stop caddy' "$migrate_case/docker.log"
grep -Fxq 'compose stop frontend worker backend' "$migrate_case/docker.log"
[[ -e "$migrate_case/migrated" ]]
python3 - "$migrate_case/docker.log" <<'PY'
from pathlib import Path
import sys

commands = Path(sys.argv[1]).read_text().splitlines()
assert commands[0] == "compose pull backend"
assert commands.index("compose pull backend") < commands.index(
    "compose --profile storage-migration run --rm --no-deps storage-owner --assert-idle --check"
)
PY

active_case="$(make_case active-after-drain)"
if (cd "$active_case" && PATH="$active_case/bin:$PATH" CHECK_MODE=active-second CHECK_COUNT="$active_case/count" DOCKER_LOG="$active_case/docker.log" MIGRATION_MARKER="$active_case/migrated" bash scripts/prepare_storage_migration.sh); then
  echo "Expected active jobs after draining ingress to stop migration." >&2
  exit 1
fi
grep -Fxq 'compose start caddy' "$active_case/docker.log"
! grep -Fq 'compose stop frontend worker backend' "$active_case/docker.log"
[[ ! -e "$active_case/migrated" ]]

echo "Storage migration preflight and idle guards passed."
