# BOQ Audit VM staging / canary preflight

Ngày: 31/10/2026; cập nhật local 02/11/2026. Phạm vi: kiểm tra preflight local và chuẩn bị lệnh cho người vận hành. **Không có SSH/GCE, không push image, không chạy migration, không bật feature flag, không sửa VM hay production.**

Mục tiêu: trước khi stage skill pin và canary worker trên VM, người vận hành có một checklist review được, biết chính xác lệnh nào chạy, kết quả mong đợi, và rollback nào áp dụng. Tài liệu này không thay thế `docs/ci-cd-gce.md`.

## 1. Trạng thái local đã xác minh (31/10/2026)

| Kiểm tra | Lệnh | Kết quả |
| --- | --- | --- |
| Deploy/storage script syntax | `bash -n scripts/deploy-gce.sh`; `bash -n scripts/migrate-storage-owner.sh` | exit 0 |
| Deploy guard + rollback fixture | `bash scripts/tests/test_deploy_gce.sh` | pass |
| Skill archive pin | `python scripts/prepare_pinned_skill.py verify` | 64 files (02/11) |
| Skill directory pin | `python scripts/prepare_pinned_skill.py verify-dir --destination <dir>` | extracted check: 64 files; host install `/home/thangpham/.agents/skills/boq-audit` passes 64-file verify (02/11); VM path not checked |
| Archive SHA-256 | `sha256sum skill-bundle/boq-audit-v6.4.zip` | `06fde007fec042d45f75a4ccb7ae96c4d13f798fd671aba174fdb2a0240d51c7` |
| Compose parse (temp copy, `.env` từ example) | `docker compose config --quiet` | exit 0; default services `backend worker frontend caddy` |
| Seccomp JSON | `python -c 'json.load(...)'` | parse OK |
| Codex CLI local canary | `python backend/scripts/canary_codex_audit_sandbox.py --parent-dir /var/tmp` (02/11) | PASS: source/skill/controller read, controller write read-only, sibling denied, output write allowed |
| Image pin | `backend/Dockerfile` | `@openai/codex@0.165.0` exact pin (xem §2) |
| Worker flag default | `.env.production.example`, `config.py`, compose | `AUDIT_CHECKPOINT_WORKER_ENABLED=false` |
| Backend image build (local, 02/11) | `docker build --network=host -t boq-audit-backend:v4-preflight backend` | exit 0; image `a1fa6b38b9af`; Codex CLI pin `0.165.0` |
| Pinned skill in v4 image (02/11) | Mount extracted bundle read-only and run `verify_pinned_bundle()` | **PASS**, current manifest matched 64-file archive |
| Worker container canary (02/11) | `docker run … --security-opt seccomp=backend/security/codex-worker-seccomp.json … python /app/scripts/canary_codex_audit_sandbox.py --parent-dir /data` | **PASS** (controller write read-only, sibling denied, source/skill read, output write) |
| Same canary without profile | Docker default seccomp | FAIL: `bwrap: No permissions to create a new namespace`; profile là bắt buộc |
| Container migration/worker smoke | `migrate_and_recover()` + `timeout -s TERM 5 python -m app.worker` | migrate exit 0; worker tạo lock/heartbeat, timeout 124 như dự kiến |
| Deploy bundle seccomp | `scripts/tests/test_deploy_gce.sh` | pass; case thiếu seccomp fail-closed; workflow copy file |

Giới hạn: image đã build và canary container đã pass **local**, nhưng chưa chạy canary trên VM/worker thật; image tag đang chạy, disk/queue thật vẫn do người vận hành kiểm trong cửa sổ maintenance.

## 2. Thay đổi local liên quan preflight

- `backend/Dockerfile` pin `@openai/codex@0.165.0` thay vì cài bản mới nhất. Đây là phiên bản đã canary local; các flag `codex sandbox --permission-profile` và `-c permissions...` nhạy cảm với phiên bản. Regression: `backend/tests/test_runtime_pins.py` khóa pin này với `audit_sandbox.CODEX_CLI_VERSION`.
- Deploy bundle nay chép `backend/security/codex-worker-seccomp.json`; `.github/workflows/ci-cd.yml` scp/install file này và `scripts/deploy-gce.sh` fail-closed nếu thiếu. Trước đây compose tham chiếu file nhưng bundle không có, nên `docker compose up` trên VM sẽ lỗi. Regression: `scripts/tests/test_deploy_gce.sh` có case thiếu seccomp.
- Không đổi runtime contract và không đổi `docker-compose.yml` trong lần sửa này.

## 3. Điều kiện vào (operator xác nhận trước khi stage)

- [ ] Chọn `IMAGE_TAG` là full commit SHA của release đã qua Tests/Build CI, không dùng `latest`.
- [ ] `BOQ_AUDIT_SKILL_HOST_PATH` trên VM trỏ tới thư mục chứa `SKILL.md` của bundle pin, mount read-only.
- [ ] `.env` production có `AUDIT_CHECKPOINT_WORKER_ENABLED=false` trong suốt staging/canary; chỉ đổi sau khi engineer acceptance.
- [ ] Có backup/snapshot persistent disk; với first cutover có thêm `.cutover/docker-compose.yml` cũ và `.cutover/backup.tar.gz` chứa `data/database/boq.db` + `data/jobs/`.
- [ ] Không có job `SUBMITTED`/`PROCESSING` khi deploy guard chạy (deploy script tự chặn; vẫn kiểm trước).
- [ ] Storage đã thuộc `10001:10001`; nếu chưa, dừng và chạy `scripts/migrate-storage-owner.sh` theo đúng thứ tự.
- [ ] `backend/security/codex-worker-seccomp.json` có trong app directory và compose resolve được; deploy workflow/script nay chép và kiểm file này trước khi pull/up.

## 4. Lệnh read-only trên VM (không thay đổi state)

Chạy trong thư mục compose (`GCE_DEPLOY_PATH`, mặc định `/home/hello291994/BOQ-Audit`):

```bash
# 1. Image và service đang chạy
docker compose ps
docker compose images
docker image inspect "$(docker compose images -q backend)" --format '{{.Id}} {{index .Config.Labels "org.opencontainers.image.revision"}}'

# 2. Skill host path và pin thực tế (chạy từ checkout đúng commit release)
echo "$BOQ_AUDIT_SKILL_HOST_PATH"
python scripts/prepare_pinned_skill.py verify-dir --destination "$BOQ_AUDIT_SKILL_HOST_PATH"
sha256sum "$BOQ_AUDIT_SKILL_HOST_PATH/SKILL.md"   # expect 64415e1369b30240c7ca3239142e9c8a7d11d0cf9d3d678624dae19cc626220f

# 3. Health và worker lock
curl -fsS https://<FRONTEND_URL>/api/health
docker compose exec worker python -m app.worker --health   # 0 = healthy/lock held
docker compose exec backend python -c 'from app.core.config import get_settings; s=get_settings(); print(s.audit_checkpoint_worker_enabled, s.boq_audit_skill_path)'

# 4. Storage ownership, seccomp profile và dung lượng
docker compose --profile storage-migration run --rm --no-deps storage-owner --check
test -f backend/security/codex-worker-seccomp.json && echo "seccomp profile present"
df -h /home/hello291994/BOQ-Audit/data
```

## 5. Stage skill pin (operator, có backup trước)

Thực hiện từ checkout đúng commit release (không copy tay từng file):

**Bài học từ pilot local 31/10/2026:** skill đang cài ở `/home/thangpham/.agents/skills/boq-audit` lệch pin ở 10 file (`SKILL.md`, `scripts/validate_run.py`, `PACKAGE_MANIFEST.txt`, ...). Một pilot chạy với skill này tạo output thật nhưng quality adapter trả `BUNDLE_INVALID` và không cấp receipt PASS. Luôn chạy `verify-dir` trước pilot/stage; không dùng bản cài cũ.

```bash
# Trên máy build/checkout đã xác minh archive
sha256sum skill-bundle/boq-audit-v6.4.zip   # expect 06fde007fec042d45f75a4ccb7ae96c4d13f798fd671aba174fdb2a0240d51c7
python scripts/prepare_pinned_skill.py verify

# Workflow deploy stage archive đã verify vào `/srv/boq-audit-skill-v6.4` (64 files), giữ nguyên thư mục pin cũ, và đổi `.env` sang pin mới sau health check thành công.

# Trỏ BOQ_AUDIT_SKILL_HOST_PATH sang thư mục mới và kiểm bằng chính backend image mới:
docker run --rm \
  -v /srv/boq-audit-skill-v6.4:/opt/codex/skills/boq-audit:ro \
  "$IMAGE_REGISTRY/backend:$IMAGE_TAG" \
  python -c 'from pathlib import Path; from app.services.audit_quality import TRUSTED_SKILL_DIGEST, verify_pinned_bundle; verify_pinned_bundle(Path("/opt/codex/skills/boq-audit"), Path("/app/app/boq_audit_skill_manifest.json"), expected_digest=TRUSTED_SKILL_DIGEST); print("pinned skill verified")'
```

`scripts/deploy-gce.sh` đã tự chạy bước verify tương đương và dừng trước khi pull/thay container nếu digest lệch.

## 6. Canary trong worker container (chưa bật audit)

Chạy sau khi image mới đã có mặt và worker healthy, flag vẫn `false`:

```bash
docker compose exec worker python /app/scripts/canary_codex_audit_sandbox.py \
  --parent-dir /data \
  --codex /usr/local/bin/codex
```

Kỳ vọng (JSON `status: PASS`):

- `source_read`, `skill_read`, `controller_read` trả đúng sentinel.
- `controller_write` chứa `Read-only file system`.
- `sibling_read` chứa `FileNotFoundError`.
- `output_write` = `allowed`.

Nếu canary fail: dừng, không bật `AUDIT_CHECKPOINT_WORKER_ENABLED`, không chạy pilot; giữ image/skill cũ. Ghi lại stdout/stderr và `docker compose logs worker --since 10m`.

Kết quả local 31/10 trên image `boq-audit-backend:pinned-preflight`: canary **PASS** với `seccomp=backend/security/codex-worker-seccomp.json`; cùng canary **FAIL** khi bỏ profile (`bwrap: No permissions to create a new namespace`). Vì vậy không thay profile bằng `seccomp=unconfined` và không bỏ `security_opt` của worker.

## 7. Observe-only rollout sau canary

1. Giữ `AUDIT_CHECKPOINT_WORKER_ENABLED=false`; deploy image mới + skill pin mới. Đây là bước observe: không job mới nào đi vào checkpoint path.
2. Chạy một job synthetic hoặc hồ sơ đã được phép trên bản sao phi production; xác nhận không có output công bố.
3. Chỉ sau engineer acceptance + corpus adjudicated mới đổi flag cho job mới. Legacy pending kiểm lại; legacy completed giữ nguyên.
4. Không tắt enforce âm thầm khi lỗi: pause audit mới, giữ drafts, sửa/rollback có kế hoạch.

## 8. Rollback reviewable

| Tình huống | Hành động | Ghi chú |
| --- | --- | --- |
| Canary fail trước khi đổi container | Không deploy; giữ image/skill cũ | Không có state change |
| Health fail sau `docker compose up` | `deploy-gce.sh` tự rollback image `rollback` cho backend/frontend/worker | Worker rollback chỉ có sau first cutover; xem log |
| Rollback image cũng health fail | Dừng app, giữ DB/jobs, kiểm tra thủ công | Không tự xóa/khôi phục dữ liệu |
| Public health fail **sau khi Caddy mở** | Script đóng ingress, giữ nguyên DB/jobs, không tự restore | Upload mới có thể đã được nhận; đối soát trước khi restore |
| First cutover fail trước khi mở Caddy | Restore `.cutover/backup.tar.gz` + compose cũ, chạy image cũ | Chỉ khi chưa có upload mới |
| Migration lỗi | Image cũ vẫn đọc được cột additive mới; dừng worker/backend và kiểm tra schema | Migration là additive: `invalidated_check_ids_json`, `invalidation_digest`, `lesson_snapshot_*`, `checker_digest`, `derived_from_run_id` |
| Storage ownership sai | Dừng backend/worker/frontend/caddy; `scripts/migrate-storage-owner.sh` (assert idle → backup → apply → check) | Script từ chối nếu service còn chạy |

Không có rollback nào tự động đảo database hoặc file. Mọi restore phải xác nhận không mất upload/job mới.

## 9. Điều kiện dừng (không được deploy)

- Canary không PASS trong container.
- Skill host path thiếu `SKILL.md` hoặc digest khác pin.
- `.env` còn `AUDIT_CHECKPOINT_WORKER_ENABLED=true` khi chưa có engineer acceptance.
- Thiếu `backend/security/codex-worker-seccomp.json` trong app directory hoặc `docker compose config` không resolve được seccomp path.
- Storage chưa thuộc `10001:10001` hoặc còn service đang chạy khi chuyển ownership.
- Không có backup/snapshot; first cutover thiếu `.cutover/` files.
- Job đang `SUBMITTED`/`PROCESSING` hoặc worker heartbeat/lock không nhất quán.

## 10. Câu hỏi còn mở cho người vận hành

1. Image tag release nào được chọn và đã có kết quả CI `Tests` + `Build container images (PR)` chưa?
2. `BOQ_AUDIT_SKILL_HOST_PATH` hiện tại trên VM là gì, và có cần giữ song song thư mục pin cũ để rollback?
3. Có cửa sổ maintenance/backup snapshot persistent disk trước lần stage này không?
4. Ai là engineer ký acceptance và bộ corpus/holdout nào được dùng trước khi đổi flag?
