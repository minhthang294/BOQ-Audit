# HANDOFF — Q00a-core (read-only, không DONE)

**Baseline:** HEAD `8cdacddc3ec8187d29d20d2bc1792197abe55bdd`, branch `boq-quality/q00-core`, `git status --short` rỗng. Manifest digest `17c14f63…14a2c` khớp TASK.md tại `.orchestration/skill-manifest.json:2`. Không sửa file khác ngoài handoff này; không chạy test.

## Điều chỉnh so với bản trước

1. **B1 đã bỏ.** Bundle tồn tại tại `.orchestration/skill` (thư mục) và `.orchestration/skill/scripts/validate_run.py` (20.412 bytes), đã xác nhận bằng `ls`; mapping chi tiết do lane lessons chịu.
2. **B3 không còn là blocker môi trường.** Venv test có sẵn: `/tmp/boq-excel-testvenv-20261023/bin/python` (pytest 8.3.5, httpx2). Test vẫn ghi **NOT_RUN** trong lượt read-only này.
3. **C1–C6, C8 phân loại lại là CURRENT so với TARGET**, không phải mâu thuẫn nội bộ của plan. Chỉ C7 còn là docs lịch sử lệch code.

## 1. Publish / sửa output — mọi đường

| Đường | path:line | Hành vi | Loại |
|---|---|---|---|
| Runner finalize | `backend/app/services/audit_runner.py:201-235` | đổi PROCESSING sang REVIEW/FAILED, ghi đè toàn bộ `JobOutput` | EXISTING |
| Xoá và thay output runner | `audit_runner.py:210-213` | `job.outputs.clear()` rồi append mọi `.xlsx/.pdf` trong `output/`; xoá cả row admin/legacy, kể cả timeout | EXISTING |
| Admin upload/replace | `backend/app/api/admin.py:160-206` | lưu `jobs/<code>/output/<uuid>.<ext>` tại `:186`; xoá row cùng `file_type` tại `:192-196`; commit `:197`; unlink file cũ ngoài `attempts/` tại `:199-201` | EXISTING |
| Admin delete output | `admin.py:209-222` | xoá row và file, cho phép trên job COMPLETED | EXISTING |
| Admin PATCH status | `admin.py:141-157` | COMPLETED sang REVIEW/WAITING_FOR_INFO/FAILED; cấm set SUBMITTED/PROCESSING `:145-146`; cấm set COMPLETED `:149-150` | EXISTING |
| Admin PATCH counters/notes | `admin.py:151-155` | sửa `critical_errors`, `warnings`, `customer_notes`, `admin_notes` trên COMPLETED, không kiểm lại | EXISTING |
| Admin complete | `admin.py:258-278` | chỉ kiểm loại output `:264-274`, set COMPLETED `:277` | EXISTING |
| Complete lần 2 | `admin.py:275-277` | trả 200, không đổi `completed_at`, không audit trail | EXISTING |
| Customer retry | `backend/app/api/jobs.py:217-246` | FAILED sang SUBMITTED, xoá rows output `:237`, reset counters `:238-244`; không xoá file | EXISTING |
| Xoá job | `admin.py:225-255`, `jobs.py:271-303` | quarantine thư mục, xoá DB; cascade rows tại `entities.py:96` | EXISTING |
| Quality gate, digest, stale check | — | không tồn tại | NEW (Q02) |
| Validator invocation | — | không tồn tại trong backend | NEW (Q01) |

Hệ quả publish: upload, delete, PATCH trên job COMPLETED thay đổi nội dung khách tải mà không quay lại REVIEW; download đọc file live tại `backend/app/storage/files.py:95-165` và `jobs.py:342-364`.

## 2. Current-run guards

- Claim: chỉ SUBMITTED, path không `pending`, file tồn tại; tạo attempt `max+1` tại `audit_runner.py:110-128`.
- Finish: newest attempt, `run.status == RUNNING`, job PROCESSING, kiểm trong `BEGIN IMMEDIATE` tại `audit_runner.py:203-209`.
- Recovery: latest RUNNING khớp job PROCESSING; requeue tối đa 1 lần tại `backend/app/core/migrations.py:35-52`.
- Admin chặn BUSY tại `admin.py:19,145-148,169-170,190-191,212-213,262-263`.
- Customer: output chỉ hiện khi COMPLETED (`jobs.py:47-48,338`); retry chỉ FAILED (`jobs.py:226`); xoá chặn PROCESSING/pending (`jobs.py:276-277`).
- Không guard nào buộc complete, upload, delete khớp `AuditRun` hiện hành, output digest, hay quality receipt.

## 3. Transaction boundaries

| Tác vụ | Boundary |
|---|---|
| Claim | `BEGIN IMMEDIATE` `audit_runner.py:109`, commit `:127` |
| Finish | `BEGIN IMMEDIATE` `:203`, commit `:234`; không giữ txn khi Codex chạy `:255` |
| Admin lock | rollback và `BEGIN IMMEDIATE` tại `admin.py:24-27` |
| Upload output | validate/save ngoài txn `admin.py:168-187`; lock lại `:189`; commit `:197`; unlink sau commit `:199-201` |
| Complete | lock `:261`, commit `:278` |
| Delete output | lock `:211`, commit `:219`, unlink `:220-221` |
| Retry | `BEGIN IMMEDIATE` `jobs.py:224`, commit `:245` |
| Recovery | `BEGIN IMMEDIATE` `migrations.py:28`, commit `:53` |

## 4. Race phải test (chưa chạy)

1. Upload vs complete: upload qua check BUSY và ghi file, complete công bố, upload lock sau thay file trên COMPLETED tại `admin.py:169-201` so với `:258-278`.
2. Delete output sau complete: job COMPLETED nhưng thiếu báo cáo bắt buộc tại `admin.py:209-222`.
3. PATCH counters/notes trên COMPLETED không kiểm lại tại `admin.py:141-157`.
4. Complete từ FAILED bằng output admin upload, bỏ qua runner/review tại `admin.py:258-274`.
5. Stale `_finish` vs retry/recovery: nền có tại `backend/tests/test_recovery.py:53-79`; chưa phủ upload/complete.
6. `_finish` vs complete: PROCESSING phải 409; chưa có test race thật.
7. Hai upload cùng `file_type`: xoá row cũ, row cuối thắng tại `admin.py:192-196`.
8. Symlink trong `output/`: `_attempt_outputs` chỉ `is_file()` tại `audit_runner.py:33-34`; path ghi DB tại `:211-213`.
9. Sửa file sau complete: ETag chỉ mtime/size tại `files.py:107-108`; không phát hiện thay nội dung.
10. Migration chạy hai lần cho bảng quality mới: chưa có test.

## 5. CURRENT so với TARGET (không phải mâu thuẫn plan)

| # | Hiện tại | Đích |
|---|---|---|
| C1 | Counters parse từ câu cuối model tại `audit_runner.py:86-103,214-217` | Counters từ ledger theo plan `:96` |
| C2 | Exit khác 0 và đủ output vào REVIEW, complete được ngay tại `audit_runner.py:226-230`, `admin.py:258-278` | Giữ draft REVIEW, quality ERROR chặn complete theo plan `:107` |
| C3 | Output/counters sửa sau công bố, không digest/stale reject tại `admin.py:141-222` | Từ chối publish khi stale theo plan `:111,115` |
| C4 | Complete không gắn run/snapshot, không phân biệt legacy tại `admin.py:258-279`, `entities.py:126-136` | Gate gắn snapshot và output digest theo plan `:113,115` |
| C5 | `AuditRun.status` không bao giờ COMPLETED khi publish tại `audit_runner.py:231-233`; test set tay tại `backend/tests/test_timing_chat.py:89` | Run/snapshot là mốc công bố |
| C6 | `work/` không được code tạo/đọc; chỉ có `output/` tại `audit_runner.py:164-166` | Có `work/` và `controller/` theo plan `:64,66` |
| C8 | Output hợp lệ chỉ theo đuôi file và tên chứa estimate, du-toan, dutoan tại `audit_runner.py:23-29`; prompt yêu cầu tên cố định `:47` nhưng không enforce | Output đúng tên/nội dung theo contract Q00b |

## 6. Conflict còn lại

| # | Conflict | Bằng chứng |
|---|---|---|
| C7 | Docs recovery/runbook mô tả hành vi cũ: BackgroundTasks, startup fail mọi job, retry xoá thư mục, quét cả cây | `docs/AUDIT_RECOVERY_PLAN.md:15-20`, `docs/quality-pilot-runbook.md:35` so với code hiện tại `worker.py`, `migrations.py:16`, `jobs.py:237`, `audit_runner.py:32-34` |

## 7. Paths work/output

- `jobs_dir = data_dir/jobs` tại `backend/app/core/config.py:89-91`; mount `/data` tại `docker-compose.yml:10,38`.
- Workspace: `jobs/<code>/attempts/<attempt>/` tại `audit_runner.py:164`; output `.../output/` tại `:165-166`; log `codex-run.jsonl`, `codex-run.stderr.log` tại `:173-174`.
- Legacy/admin: `jobs/<code>/output/<uuid>.<ext>` tại `admin.py:186`.
- `work/`: NEW, chưa có trong backend; là convention bundle theo plan `:64`.
- Containment download: `files.py:87-92`, `admin.py:45-52`; runner outputs không kiểm containment.

## 8. NOT_VERIFIED

- Mapping validator/ledger chi tiết trong `.orchestration/skill`: NOT_VERIFIED trong lane này, do lane lessons chịu.
- Hành vi filesystem/CLI thực tế trong container: NOT_VERIFIED, chỉ suy từ compose và code.
- Full frontend flow: NOT_VERIFIED; chỉ đọc `frontend/app/admin/jobs/[job_code]/page.tsx:30-40`.
- DB, data, exports, logs, .env: NOT_VERIFIED theo phạm vi.

## 9. Test commands — NOT_RUN

```bash
cd backend && PYTHONPATH=. /tmp/boq-excel-testvenv-20261023/bin/python -m pytest -q
cd backend && PYTHONPATH=. /tmp/boq-excel-testvenv-20261023/bin/python -m pytest -q tests/test_jobs.py tests/test_recovery.py tests/test_timing_chat.py
cd backend && /tmp/boq-excel-testvenv-20261023/bin/python -m compileall -q app
# sau Q02:
cd backend && PYTHONPATH=. /tmp/boq-excel-testvenv-20261023/bin/python -m pytest -q tests/test_audit_quality.py
```

## 10. Bước tiếp theo

Không đánh Q00 DONE, không bắt đầu Q01/Q08/Q10. Lead chốt C1–C8 (C1–C6, C8 là CURRENT so với TARGET), C7, và unique constraints cho `audit_quality_reviews` ở Q00b. B2 vẫn cần lead quyết định: model chạy subprocess cùng container, cùng mount `./data:/data` tại `docker-compose.yml:37-40` và `audit_runner.py:176-179`, nên receipt controller-owned chưa enforceable.
