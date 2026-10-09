#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT

make_case() {
  local name="$1"
  local dir="$test_root/$name"
  mkdir -p "$dir/scripts" "$dir/bin"
  cp "$repo_root/scripts/deploy-gce.sh" "$dir/scripts/deploy-gce.sh"
  printf 'APP_ENV=production\nFRONTEND_URL=https://boq.example.com\n' > "$dir/.env"
  printf 'IMAGE_REGISTRY=us-central1-docker.pkg.dev/test-project/boq-audit\nIMAGE_TAG=0123456789abcdef0123456789abcdef01234567-123456-1\n' > "$dir/.deploy.env"
  cat > "$dir/bin/docker" <<'FAKE_DOCKER'
#!/usr/bin/env bash
set -euo pipefail
printf '%s|%s\n' "${IMAGE_TAG:-}" "$*" >> "$FAKE_DOCKER_LOG"
case "$*" in
  "compose config --quiet"|"compose pull backend frontend chat"|"compose up -d --no-build --remove-orphans --wait --wait-timeout 240"|"compose logs --tail=100 backend frontend chat caddy"|"image tag "*)
    if [[ "$*" == "compose up "* && "${IMAGE_TAG:-}" != rollback && "${FAKE_FAIL_DEPLOY:-0}" == 1 ]]; then exit 1; fi
    ;;
  "compose ps --status running --services") printf 'backend\n' ;;
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

success_case="$(make_case success)"
(cd "$success_case" && PATH="$success_case/bin:$PATH" FAKE_DOCKER_LOG="$success_case/docker.log" bash scripts/deploy-gce.sh)
grep -Fq '0123456789abcdef0123456789abcdef01234567-123456-1|compose up ' "$success_case/docker.log"

rollback_case="$(make_case rollback)"
if (cd "$rollback_case" && PATH="$rollback_case/bin:$PATH" FAKE_DOCKER_LOG="$rollback_case/docker.log" FAKE_FAIL_DEPLOY=1 bash scripts/deploy-gce.sh); then
  echo "Expected the failed release to return a failure status after rollback." >&2
  exit 1
fi
grep -Fq 'rollback|compose up ' "$rollback_case/docker.log"

echo "Deploy guard, successful health path, and failed-release rollback checks passed."
