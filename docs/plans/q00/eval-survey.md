# Q00a-eval — Handoff khảo sát chỉ đọc

## 1. Baseline và phạm vi

- HEAD: `8cdacddc3ec8187d29d20d2bc1792197abe55bdd`; branch `boq-quality/q00-eval`; `git status --short` sạch trước khi tạo handoff này.
- Skill snapshot digest `17c14f63f78c2670428e233c4bfc56623473d1d644cf38d003db477d80c14a2c`: đã tính lại, khớp 62/62 file và khớp `.orchestration/skill-manifest.json:2`.
- Chỉ đọc trong checkout. Không đọc `.env`, credential, `audit_runs`, exports, raw logs; không chạy model, Docker, production hay full test.
- **Q00 chưa DONE.** Không bắt đầu Q01/Q08/Q10.

## 2. Mapping runtime/schema thực tế

| Hạng mục | Hiện có | Bằng chứng |
|---|---|---|
| Workspace attempt | `jobs/<code>/attempts/<attempt>/`; `output/` là sibling | `backend/app/services/audit_runner.py:164-166` |
| `work/`, `controller/` | Không tồn tại trong runtime | `audit_runner.py:162-198` |
| Quyền ghi của model | Toàn bộ workspace; chỉ ràng buộc bằng prompt | `audit_runner.py:40-47` |
| Kết thúc run | Chỉ kiểm loại output; đủ file ⇒ REVIEW, thiếu/None ⇒ FAILED | `audit_runner.py:201-235`, nhất là `218-225` |
| Complete | Chỉ kiểm `EXCEL_REPORT`, `ANNOTATED_PDF`, `ESTIMATE_REPORT` | `backend/app/api/admin.py:258-279` |
| Sửa output/status | Upload thay output cũ; PATCH đổi status/counters, chặn COMPLETED | `admin.py:141-157`, `160-206` |
| DB quality | Không có bảng quality/receipt/snapshot; chỉ `create_all` | `backend/app/models/entities.py:126-211`; `backend/app/core/migrations.py:10` |
| Skill path | Ngoài repo, mặc định `/opt/codex/skills/boq-audit/SKILL.md` | `backend/app/core/config.py:36-37` |
| Compose | `./data:/data` RW + Codex home cho backend/worker | `docker-compose.yml:9-13,37-40` |
| Validator | Chỉ đọc JSON `stem.json`, không đọc CSV/templates | `.orchestration/skill/scripts/validate_run.py:22-38` |
| Gate | pass1/2/3/release; exit 2 nếu BLOCKED; ghi result vào workdir | `validate_run.py:31,276-294` |
| JSON Schema | 8 schema có sẵn nhưng validator không dùng `jsonschema` | `validate_run.py:6`; `.orchestration/skill/schemas/*` |
| Sanity checks | Chỉ `DISCRETE_FRACTION` và STT; không tính lại tổng/nhân/đơn vị | `.orchestration/skill/scripts/sanity_checks.py:8-27` |
| Inventory | Tạo 11 JSON + manifest/checkpoint/23 R-*; từ chối ghi đè | `.orchestration/skill/scripts/inventory_pdf.py:17-39` |
| R-* | 23 case R-01…R-23 | `.orchestration/skill/references/regression.md:5-83` |

## 3. Fixtures hiện có

- Fixture synthetic đầy đủ inline: 1 trang, 23 R-* NA, deliverables sibling — `.orchestration/skill/tests/test_workflow.py:25-54` (EXISTING, không phải fixture file).
- Test chặn đóng case khi thiếu target — `test_workflow.py:103-120`.
- Backend chỉ có PDF tối thiểu `b"%PDF-1.4…"` — `backend/tests/conftest.py:114-116`; XLSX giả `PK\x03\x04` — `backend/tests/test_jobs.py:84-93`.
- **Không có** `evaluation/`, corpus nhãn, holdout, scorer, reference/adjudication schema hay fixture Q10.

## 4. Failure matrix: khoảng trống

| Tình huống plan §6 | Test hiện có | Kết luận |
|---|---|---|
| Đủ Excel/PDF nhưng thiếu ledger/pass2 | `test_jobs.py:186-210` chỉ kiểm output | Thiếu |
| Model ghi PASS giả trong work | Không | Thiếu |
| Pass1 thiếu evidence 1 cấu kiện | Validator có page/evidence; backend không | Thiếu backend |
| Đủ trang nhưng sót 1 dòng detail | Chỉ so `detail_rows_total`; không inventory dòng | Thiếu |
| Tái kiểm cùng context | Không | Thiếu |
| Case 12 target chỉ kiểm 11 | `test_workflow.py:113-120` | Chỉ skill |
| Nguồn/giả thiết đổi sau gate | Chỉ `pending_rechecks` skill; backend không invalidation | Thiếu |
| Worker chết lúc ghi | `test_recovery.py:53-93` bảo toàn attempt; chưa snapshot controller | Một phần |
| False positive bị bác bỏ | Không | Thiếu |
| Memory lỗi | Chỉ validator disposition | Thiếu service |
| Model approve lesson | Không có lesson store | Thiếu |
| Reference bị model thấy | Skill mount RO nhưng `/data` RW; không canary | Blocker isolation |
| Sót critical reference | Không scorer | Thiếu |
| Admin sửa report sau ký | `test_jobs.py:163-177` chỉ ẩn output khi reopen | Thiếu digest/signoff |
| Customer đọc receipt người khác | Chỉ IDOR job/output | Thiếu receipt |

**Isolation thực tế:** không có controller receipts, pass2 blind hay holdout. Model thấy skill references/regression và toàn `/data`; prompt chỉ hướng dẫn (`audit_runner.py:40-47`). Plan yêu cầu chưa coi receipt đáng tin nếu chưa cưỡng chế (`docs/plans/BOQ_AUDIT_QUALITY_PLAN.md:66,329`).

## 5. Contract conflicts cần lead chốt

1. **CSV vs JSON:** templates là CSV (`templates/*.csv`), validator chỉ đọc JSON (`validate_run.py:22-38`); inventory tạo JSON (`inventory_pdf.py:17-39`).
2. **Template không tự pass:** `templates/run_manifest.json:38` để `memory_lookup.status="NOT_CHECKED"`, validator chỉ nhận FOUND/NO_MATCH/UNAVAILABLE (`validate_run.py:71-77`); nhiều count/flag null/false.
3. **Tên deliverable:** prompt bắt `BOQ_Audit_Report.xlsx`, `Annotated_Original.pdf` (`audit_runner.py:47`) ≠ skill `Bao_cao_<Ten>.xlsx`, `Ban_ve_..._Danh_dau_loi.pdf` (`references/data-and-delivery.md:29-38`).
4. **Phiên bản:** runbook yêu cầu `v6.3` (`docs/quality-pilot-runbook.md:85`), validator bắt/trả `v6.4` (`validate_run.py:79,278`).
5. **Digest validator:** verification ghi `8bca7e…` (`docs/quality-pilot-verification.md:9`); thực tế `86839c…`, khớp manifest.
6. **Số test:** verification ghi 57 (`quality-pilot-verification.md:29`); hiện có 61 `def test`, không skip/xfail.
7. **Schema lệch validator:** `issue_ledger` yêu cầu `status`, validator xét `final_status`; `pass_review.result` schema string nhưng validator chỉ cần truthy; coverage CSV dùng `detail_summary_reconciliation_complete`, validator dùng `summary_reconciliation_complete`.
8. **Receipt ownership:** validator mặc định ghi result ngay trong workdir model ghi được (`validate_run.py:291-293`).

## 6. Đề xuất schema tối thiểu — NEW

**`evaluation/schemas/reference.schema.json` (NEW)**
`schema_version, corpus_id, package_id, case_type {CONFIRMED_ERROR, SCOPED_CLEAN, AMBIGUOUS, SYNTHETIC_SEED}, discipline, source {doc_id, revision, sha256}, scope, exclusions[], required_checks[], limitations[], ref_id, root_ref_id, object_id, error_class, severity, location {doc_id, revision, pdf_page, printed_sheet, boq_sheet, row, cell}, basis {method, formula, inputs[], units, assumptions[], tolerance}, evidence_refs[], reviewer_id, adjudication_status {ADJUDICATED, UNRESOLVED, EXCLUDED}, version, supersedes`.

**`evaluation/schemas/adjudication.schema.json` (NEW)**
`attempt_id, run_record_id, finding_id, root_finding_id, ref_id|null (tối đa 1), match {TP, FP, UNRESOLVED, OUT_OF_SCOPE}, reason, reviewer_id, decided_at, label_version, source_hash`. Duplicate/split chỉ tính theo `root_finding_id`.

**`evaluation/schemas/run_record.schema.json` (NEW)**
`app_revision, deployment_revision, cli_version, model_id, skill_digest, prompt_sha256, config_digest, source_hashes{}, attempt_status {COMPLETED, PARTIAL, FAILED, BLOCKED}, artifact_hashes{}, metrics {tp, fp, fn, unresolved, unscored, precision, recall, denominator_note}`; mẫu số 0 = `N/A`.

## 7. Fixtures tổng hợp Q00/Q10 — NEW

| ID | Nội dung | Kỳ vọng |
|---|---|---|
| F-Q00-valid | 1 PDF/1 trang, đủ JSON, 23 R-* NA có anchor, memory UNAVAILABLE, deliverable sibling | exit 0, `METADATA_STAGE_COMPLETE` |
| F-Q00-template-as-is | Chạy inventory rồi gate ngay | BLOCKED: memory NOT_CHECKED + count null + flags false |
| F-Q00-csv-only | Chỉ copy templates CSV | BLOCKED: thiếu JSON |
| F-Q00-fake-pass | Có `coverage_result.json`/PASS flags nhưng thiếu ledger | BLOCKED; không tạo receipt |
| F-Q00-path | Symlink/traversal/absolute `--out` | reject trước validator |
| F-Q10-three | Confirmed error + scoped clean + ambiguous; nhãn ngoài runtime | TP/FP/FN đúng, synthetic tách mẫu số |
| F-Q10-edge | Duplicate finding, novel chưa duyệt, revision mismatch, failed run, 0 mẫu số | không tăng TP; N/A; attempt vẫn hiện |

## 8. Lệnh test dự kiến — NOT_RUN

Lead đã xác minh venv `/tmp/boq-excel-testvenv-20261023/bin/python` có pytest 8.3.5, httpx2 2.13.0 và dependencies khớp. Dùng venv này cho lượt chạy sau; không cài thêm trong lượt khảo sát này.

```sh
# Targeted, tuần tự, không xdist
cd backend && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  TELEGRAM_BOT_TOKEN= TELEGRAM_CHAT_ID= CHAT_GATEWAY_TOKEN= \
  /tmp/boq-excel-testvenv-20261023/bin/python -m pytest -q \
  -p no:cacheprovider -p no:xdist \
  tests/test_jobs.py tests/test_recovery.py tests/test_timing_chat.py

# Toàn bộ backend, tuần tự
cd backend && PYTHONPATH=. \
  /tmp/boq-excel-testvenv-20261023/bin/python -m pytest -q -p no:xdist

# Skill tests (stdlib, synthetic metadata)
cd .orchestration/skill && python3 -m unittest discover tests -v

# Validator pinned
python3 .orchestration/skill/scripts/validate_run.py <workdir> --stage pass1
python3 .orchestration/skill/scripts/validate_run.py <workdir> --out coverage_result.json
```

**Serialize backend bắt buộc:** `backend/tests/conftest.py:8` dùng chung `/tmp/boq-audit-tests`; fixture autouse `drop_all` và xóa `jobs/`, `excel/` (`conftest.py:30-51`). Không chạy song song/xdist. Chưa sửa; Q00b/Q01 nên thêm lock hoặc marker serial.

## 9. Blockers và giới hạn môi trường

- **Đã gỡ:** thiếu pytest/httpx2 — venv lead xác minh có đủ; dùng path trên cho lần chạy test.
- **Còn giới hạn:** venv là Python 3.14, CI dùng Python 3.12 (`/.github/workflows/ci-cd.yml`). Kết quả local không thay thế CI; ghi rõ phiên bản khi chạy.
- **Chưa có implementation:** `audit_quality.py`, bảng `audit_quality_reviews`, receipt/snapshot controller, coverage service, reconciliation. Đây là **gap mục tiêu của Q00/Q01–Q03**, không tự coi là blocker cản Q01 synthetic.
- **Isolation chưa cưỡng chế:** compose mount `/data` RW; thiếu canary/boundary. Blocker cho Q02 enforce/Q05 blind/Q11 live, không cản unit/synthetic Q01.
- Bundle skill ngoài repo, chưa pin digest vào runtime.
- Validator không validate JSON Schema; adapter Q01 phải tự kiểm field/version.

## 10. Chưa kiểm và bước tiếp

- **NOT_RUN:** toàn bộ lệnh test, validator CLI, Docker/compose, frontend, live GCP. Không suy ra pass từ tài liệu cũ.
- Chưa kiểm race filesystem, symlink thực tế, credential isolation, quyền admin API trên deployment.
- Q00b cần lead quyết: JSON là nguồn sự thật; tên deliverable; pin bundle/digest; schema reference/adjudication; 3 fixture synthetic; điều kiện isolation. Sau nghiệm thu mới giao Q01a.
