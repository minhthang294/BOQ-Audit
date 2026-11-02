#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT

make_case() {
  local name="$1"
  local dir="$test_root/$name"
  mkdir -p "$dir/scripts" "$dir/bin" "$dir/skill" "$dir/skill-next" "$dir/backend/security"
  printf 'synthetic skill\n' > "$dir/skill/SKILL.md"
  printf 'new synthetic skill\n' > "$dir/skill-next/SKILL.md"
  cp "$repo_root/scripts/deploy-gce.sh" "$dir/scripts/deploy-gce.sh"
  cp "$repo_root/backend/security/codex-worker-seccomp.json" "$dir/backend/security/codex-worker-seccomp.json"
  printf 'APP_ENV=production\nFRONTEND_URL=https://boq.example.com\nCHAT_GATEWAY_TOKEN=\nBOQ_AUDIT_SKILL_HOST_PATH=/tmp/fake-skill\n' > "$dir/.env"
  printf 'IMAGE_REGISTRY=us-central1-docker.pkg.dev/test-project/boq-audit\nIMAGE_TAG=0123456789abcdef0123456789abcdef01234567-123456-1\n' > "$dir/.deploy.env"
  cat > "$dir/bin/docker" <<'FAKE_DOCKER'
#!/usr/bin/env bash
set -euo pipefail
printf '%s|%s\n' "${IMAGE_TAG:-}" "$*" >> "$FAKE_DOCKER_LOG"
case "$*" in
  "run --rm --network none --read-only --user 10001:10001 --cap-drop ALL --security-opt no-new-privileges:true -v "*) [[ "${FAKE_SKILL_PIN_CHECK:-0}" == 0 ]] ;;
  "compose config --format json")
    skill="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/skill"
    [[ "${FAKE_MISSING_SKILL:-0}" == 0 ]] || skill="$skill/missing"
    printf '{"services":{"backend":{"volumes":[{"target":"/opt/codex/skills/boq-audit","source":"%s"}]}}}\n' "$skill"
    ;;
  "compose --profile storage-migration run --rm --no-deps storage-owner --check") [[ "${FAKE_STORAGE_CHECK:-0}" == 0 ]] ;;
  "compose config --quiet"|"compose pull backend frontend"|"compose pull worker"|"compose stop caddy"|"compose stop worker backend frontend"|"compose up -d --no-build --wait --wait-timeout 240 backend worker frontend"|"compose --profile chat pull chat"|"compose --profile chat-setup run --rm chat-auth-init"|"compose --profile chat stop chat"|"compose up -d --no-build --remove-orphans --wait --wait-timeout 240"|"compose --profile chat up -d --no-build --remove-orphans --wait --wait-timeout 240"|"compose logs --tail=100 backend frontend worker chat caddy"|"image tag "*)
    if [[ "$*" == "compose up "* && "${IMAGE_TAG:-}" != rollback && "${FAKE_FAIL_DEPLOY:-0}" == 1 ]]; then exit 1; fi
    ;;
  "compose exec -T frontend node -e "*) ;;
  "compose ps --status running --services") [[ "${FAKE_BACKEND_RUNNING:-1}" == 0 ]] || printf 'backend\n'; [[ "${FAKE_WORKER_RUNNING:-1}" == 0 ]] || printf 'worker\n' ;;
  "compose ps --all --services") [[ "${FAKE_WORKER_EXISTS:-1}" == 0 ]] || printf 'worker\n' ;;
  "compose --profile chat ps --status running --services") ;;
  "compose exec -T backend python -c "*) printf '%s\n' "${FAKE_ACTIVE_COUNTS:-0:0}" ;;
  "compose images --quiet "*) printf 'sha256:0123456789abcdef\n' ;;
  *) echo "Unexpected docker command: $*" >&2; exit 2 ;;
esac
FAKE_DOCKER
  cat > "$dir/bin/curl" <<'FAKE_CURL'
#!/usr/bin/env bash
exit "${FAKE_CURL_EXIT:-0}"
FAKE_CURL
  chmod +x "$dir/bin/docker" "$dir/bin/curl"
  printf 'services:\n  backend:\n' > "$dir/docker-compose.yml"
  printf '%s\n' "$dir"
}

active_case="$(make_case active)"
if (cd "$active_case" && PATH="$active_case/bin:$PATH" FAKE_DOCKER_LOG="$active_case/docker.log" FAKE_ACTIVE_COUNTS=1:0 bash scripts/deploy-gce.sh); then
  echo "Expected an active audit to block deployment." >&2
  exit 1
fi
if grep -Fq 'compose up ' "$active_case/docker.log"; then
  echo "Deployment restarted containers while an audit was active." >&2
  exit 1
fi

storage_case="$(make_case storage-owner-mismatch)"
if (cd "$storage_case" && PATH="$storage_case/bin:$PATH" FAKE_DOCKER_LOG="$storage_case/docker.log" FAKE_STORAGE_CHECK=1 bash scripts/deploy-gce.sh); then
  echo "Expected storage ownership mismatch to block deployment." >&2
  exit 1
fi
if grep -Fq 'compose up ' "$storage_case/docker.log"; then
  echo "Deployment restarted containers with incorrect storage ownership." >&2
  exit 1
fi

missing_skill_case="$(make_case missing-skill-mount)"
if (cd "$missing_skill_case" && PATH="$missing_skill_case/bin:$PATH" FAKE_DOCKER_LOG="$missing_skill_case/docker.log" FAKE_MISSING_SKILL=1 bash scripts/deploy-gce.sh); then
  echo "Expected a missing skill mount to block deployment." >&2
  exit 1
fi
if grep -Eq 'compose pull|compose up ' "$missing_skill_case/docker.log"; then
  echo "Deployment pulled or restarted containers with a missing skill mount." >&2
  exit 1
fi

skill_pin_case="$(make_case skill-pin-mismatch)"
if (cd "$skill_pin_case" && PATH="$skill_pin_case/bin:$PATH" FAKE_DOCKER_LOG="$skill_pin_case/docker.log" FAKE_SKILL_PIN_CHECK=1 bash scripts/deploy-gce.sh); then
  echo "Expected an unpinned skill bundle to block deployment." >&2
  exit 1
fi
if grep -Fq 'compose up ' "$skill_pin_case/docker.log"; then
  echo "Deployment restarted containers with an unpinned skill bundle." >&2
  exit 1
fi

missing_seccomp_case="$(make_case missing-seccomp-profile)"
rm "$missing_seccomp_case/backend/security/codex-worker-seccomp.json"
if (cd "$missing_seccomp_case" && PATH="$missing_seccomp_case/bin:$PATH" FAKE_DOCKER_LOG="$missing_seccomp_case/docker.log" bash scripts/deploy-gce.sh); then
  echo "Expected a missing worker seccomp profile to block deployment." >&2
  exit 1
fi
if [[ -e "$missing_seccomp_case/docker.log" ]] && grep -Eq 'compose pull|compose up ' "$missing_seccomp_case/docker.log"; then
  echo "Deployment pulled or restarted containers without the worker seccomp profile." >&2
  exit 1
fi
grep -Fq 'backend/security/codex-worker-seccomp.json' "$repo_root/.github/workflows/ci-cd.yml"
grep -Fq 'skill-bundle/boq-audit-v6.4.zip' "$repo_root/.github/workflows/ci-cd.yml"
grep -Fq 'extract --destination /srv/boq-audit-skill-v6.4' "$repo_root/.github/workflows/ci-cd.yml"
grep -Fq 'rmdir --ignore-fail-on-non-empty /srv/boq-audit-skill-v6.4' "$repo_root/.github/workflows/ci-cd.yml"
grep -Fq 'chown -R 10001:10001 /srv/boq-audit-skill-v6.4' "$repo_root/.github/workflows/ci-cd.yml"
grep -Fq 'storage-owner --assert-idle --check' "$repo_root/.github/workflows/ci-cd.yml"
grep -Fq 'migrate-storage-owner.sh' "$repo_root/.github/workflows/ci-cd.yml"
if grep -Fq "sudo -u '#10001'" "$repo_root/.github/workflows/ci-cd.yml"; then
  echo "Workflow uses a sudo UID syntax unsupported on the GCE VM." >&2
  exit 1
fi

success_case="$(make_case success)"
(cd "$success_case" && PATH="$success_case/bin:$PATH" FAKE_DOCKER_LOG="$success_case/docker.log" bash scripts/deploy-gce.sh)
grep -Fq '0123456789abcdef0123456789abcdef01234567-123456-1|compose up ' "$success_case/docker.log"
if grep -Fq 'compose --profile chat-setup run --rm chat-auth-init' "$success_case/docker.log"; then
  echo "Disabled chat profile initialized credentials unexpectedly." >&2
  exit 1
fi

skill_stage_case="$(make_case skill-stage)"
printf 'BOQ_AUDIT_SKILL_HOST_PATH=%s/skill-next\n' "$skill_stage_case" >> "$skill_stage_case/.deploy.env"
(cd "$skill_stage_case" && PATH="$skill_stage_case/bin:$PATH" FAKE_DOCKER_LOG="$skill_stage_case/docker.log" bash scripts/deploy-gce.sh)
grep -Fq "BOQ_AUDIT_SKILL_HOST_PATH=$skill_stage_case/skill-next" "$skill_stage_case/.env"
[[ -f "$skill_stage_case/skill/SKILL.md" ]]

skill_stage_rollback_case="$(make_case skill-stage-rollback)"
printf 'BOQ_AUDIT_SKILL_HOST_PATH=%s/skill-next\n' "$skill_stage_rollback_case" >> "$skill_stage_rollback_case/.deploy.env"
if (cd "$skill_stage_rollback_case" && PATH="$skill_stage_rollback_case/bin:$PATH" FAKE_DOCKER_LOG="$skill_stage_rollback_case/docker.log" FAKE_FAIL_DEPLOY=1 bash scripts/deploy-gce.sh); then
  echo "Expected failed deployment with a staged skill to roll back." >&2
  exit 1
fi
grep -Fq 'BOQ_AUDIT_SKILL_HOST_PATH=/tmp/fake-skill' "$skill_stage_rollback_case/.env"
grep -Fq 'rollback|compose up ' "$skill_stage_rollback_case/docker.log"

stopped_worker_case="$(make_case stopped-existing-worker)"
(cd "$stopped_worker_case" && PATH="$stopped_worker_case/bin:$PATH" FAKE_DOCKER_LOG="$stopped_worker_case/docker.log" FAKE_BACKEND_RUNNING=0 FAKE_WORKER_RUNNING=0 bash scripts/deploy-gce.sh)
if grep -Fq 'compose stop caddy' "$stopped_worker_case/docker.log"; then
  echo "A stopped existing worker was incorrectly treated as a first cutover." >&2
  exit 1
fi

chat_case="$(make_case chat-enabled)"
sed -i 's/^CHAT_GATEWAY_TOKEN=$/CHAT_GATEWAY_TOKEN=0123456789abcdef0123456789abcdef/' "$chat_case/.env"
(cd "$chat_case" && PATH="$chat_case/bin:$PATH" FAKE_DOCKER_LOG="$chat_case/docker.log" bash scripts/deploy-gce.sh)
grep -Fq 'compose --profile chat pull chat' "$chat_case/docker.log"
grep -Fq 'compose --profile chat-setup run --rm chat-auth-init' "$chat_case/docker.log"
grep -Fq 'compose --profile chat up -d --no-build --remove-orphans --wait --wait-timeout 240' "$chat_case/docker.log"

rollback_case="$(make_case rollback)"
if (cd "$rollback_case" && PATH="$rollback_case/bin:$PATH" FAKE_DOCKER_LOG="$rollback_case/docker.log" FAKE_FAIL_DEPLOY=1 bash scripts/deploy-gce.sh); then
  echo "Expected the failed release to return a failure status after rollback." >&2
  exit 1
fi
grep -Fq 'rollback|compose up ' "$rollback_case/docker.log"
grep -Fq 'image tag sha256:0123456789abcdef boq-audit/worker:rollback' "$rollback_case/docker.log"

missing_case="$(make_case missing-cutover-backup)"
if (cd "$missing_case" && PATH="$missing_case/bin:$PATH" FAKE_DOCKER_LOG="$missing_case/docker.log" FAKE_WORKER_RUNNING=0 FAKE_WORKER_EXISTS=0 bash scripts/deploy-gce.sh); then
  echo "Expected first cutover without paired backup and previous Compose to fail closed." >&2
  exit 1
fi
if grep -Fq 'compose up ' "$missing_case/docker.log"; then
  echo "First cutover changed containers without rollback artifacts." >&2
  exit 1
fi

cutover_case="$(make_case cutover-rollback)"
mkdir -p "$cutover_case/.cutover" "$cutover_case/data/database" "$cutover_case/data/jobs"
printf 'services:\n  backend:\n' > "$cutover_case/.cutover/docker-compose.yml"
printf 'old-db' > "$cutover_case/data/database/boq.db"
tar -C "$cutover_case" -czf "$cutover_case/.cutover/backup.tar.gz" data/database/boq.db data/jobs
mkdir -p "$cutover_case/scripts"
cat > "$cutover_case/scripts/restore.sh" <<'FAKE_RESTORE'
#!/usr/bin/env bash
printf '%s\n' "$1" >> "$FAKE_RESTORE_LOG"
FAKE_RESTORE
chmod +x "$cutover_case/scripts/restore.sh"
printf 'services:\n  worker:\n' > "$cutover_case/docker-compose.yml"
if (cd "$cutover_case" && PATH="$cutover_case/bin:$PATH" FAKE_DOCKER_LOG="$cutover_case/docker.log" FAKE_RESTORE_LOG="$cutover_case/restore.log" FAKE_BACKEND_RUNNING=0 FAKE_WORKER_RUNNING=0 FAKE_WORKER_EXISTS=0 FAKE_FAIL_DEPLOY=1 bash scripts/deploy-gce.sh); then
  echo "Expected failed first cutover to return failure after restoring previous release." >&2
  exit 1
fi
grep -Fq 'compose stop worker backend frontend' "$cutover_case/docker.log"
grep -Fq 'backup.tar.gz' "$cutover_case/restore.log"
grep -Fq 'rollback|compose up ' "$cutover_case/docker.log"
cmp "$cutover_case/.cutover/docker-compose.yml" "$cutover_case/docker-compose.yml"

public_case="$(make_case public-health-fail)"
mkdir -p "$public_case/.cutover" "$public_case/data/database" "$public_case/data/jobs"
printf 'services:\n  backend:\n' > "$public_case/.cutover/docker-compose.yml"
printf 'old-db' > "$public_case/data/database/boq.db"
tar -C "$public_case" -czf "$public_case/.cutover/backup.tar.gz" data/database/boq.db data/jobs
printf 'services:\n  worker:\n' > "$public_case/docker-compose.yml"
if (cd "$public_case" && PATH="$public_case/bin:$PATH" FAKE_DOCKER_LOG="$public_case/docker.log" FAKE_BACKEND_RUNNING=0 FAKE_WORKER_RUNNING=0 FAKE_WORKER_EXISTS=0 FAKE_CURL_EXIT=1 bash scripts/deploy-gce.sh); then
  echo "Expected first cutover with failed public health to fail closed." >&2
  exit 1
fi
if [[ -e "$public_case/restore.log" ]] || grep -Fq 'rollback|compose up ' "$public_case/docker.log"; then
  echo "Public health failure restored a stale backup after ingress opened." >&2
  exit 1
fi
cmp "$public_case/docker-compose.yml" <(printf 'services:\n  worker:\n')
python3 - "$public_case/docker.log" <<'PY'
from pathlib import Path
import sys
lines = Path(sys.argv[1]).read_text().splitlines()
commands = [line.split("|", 1)[1] for line in lines]
close = commands.index("compose stop caddy")
local = commands.index("compose up -d --no-build --wait --wait-timeout 240 backend worker frontend")
open_ingress = commands.index("compose up -d --no-build --remove-orphans --wait --wait-timeout 240")
assert close < local < open_ingress < commands.index("compose stop caddy", open_ingress)
assert any(command.startswith("compose exec -T frontend node -e ") for command in commands[local:open_ingress])
PY

echo "Deploy guard, worker release, and first-cutover rollback checks passed."
