# Q00a-lessons — Handoff khảo sát read-only

## 0. Baseline
- `git rev-parse HEAD`: `8cdacddc3ec8187d29d20d2bc1792197abe55bdd` — khớp baseline.
- `git status --short`: sạch. Branch `boq-quality/q00-lessons`.
- Skill manifest digest `17c14f63…`: 62/62 file khớp SHA-256, không file ngoài manifest.
- Không sửa bundle/runtime, không chạy test, không gọi subagent.

## 1. Hợp đồng CLI / exit / stdout (đã kiểm trực tiếp)

| Mục | Thực tế | Nguồn |
|---|---|---|
| CLI | `validate_run.py WORK [--stage pass1\|pass2\|pass3\|release] [--out NAME]` | `validate_run.py:284-290` |
| Output file | `coverage_result.json` (release) / `stage_<stage>_result.json`, ghi vào `WORK` | `:291-292` |
| Exit | `BLOCKED` → 2; mọi trạng thái khác → 0; exception không bắt → 1 | `:294` |
| Status pass1-3 | `BLOCKED` / `METADATA_STAGE_COMPLETE` | `:277` |
| Status release | `BLOCKED` / `PARTIAL_WITH_OPEN_ITEMS_REVIEW_REQUIRED` / `READY_FOR_ENGINEER_REVIEW` | `:276` |
| Result file keys | protocol_version, execution_contract, stage, gate, candidate_release_status, automatic_publication_allowed, human_engineer_signoff_required, open_issue_count, open_record_count, blocking_checks, warning | `:278-282` |
| **Stdout** | JSON gồm `stage`, `candidate_release_status`, `open_record_count`, **`blocking_checks` là mảng đầy đủ**, `report` (path) | `validate_run.py:293` |

**Đính chính docs:** `docs/quality-pilot-runbook.md:85` nói "stdout cũng chỉ ghi số lượng `blocking_checks`, không phải mảng" — **sai**. Stdout in nguyên mảng; không có `open_issue_count`, `gate` hay `warning` trong stdout. Runbook cùng dòng còn đòi `protocol_version: "v6.3"` trong khi validator trả `v6.4` (`validate_run.py:79,278`).

## 2. Mapping artifact thật

| Artifact validator đọc | Nơi tạo | Schema | Template | Ghi chú |
|---|---|---|---|---|
| `run_manifest.json` | `inventory_pdf.py:32-35` | — | `templates/run_manifest.json` | thiếu key `no_*_reason` dù validator đọc |
| `checkpoint.json` | `inventory_pdf.py:37` | — | `templates/checkpoint.json` | |
| `page_ledger.json` | `:35` | `page_ledger` | `page_ledger.csv` | CSV thiếu `source_pdf`, `evidence_anchors` |
| `detail_rows.json` | `:36` | **không có** | **không có** | validator tự định nghĩa field |
| `summary_lineage.json` | `:36` | `summary_lineage_ledger` | `summary_lineage_ledger.csv` | lệch tên stem |
| `calculation_ledger.json` | `:36` | `calculation_ledger` | `calculation_ledger.csv` | CSV thiếu 3 field mới |
| `assumption_ledger.json` | `:36` | `assumption_ledger` | `assumption_ledger.csv` | |
| `issues.json` | `:36` | `issue_ledger` | `issue_ledger.csv` | lệch tên + status/final_status |
| `pass2_review.json`, `pass3_review.json` | `:36` | `pass_review` | **không có** | |
| `annotation_qa.json` | `:36` | **không có** | **không có** | gate chỉ 4 field |
| `regression_checks.json` | `inventory_pdf.py:38-39` | **không có** | **không có** | sinh từ 23 heading R-* |
| `coverage_result.json` / `stage_*_result.json` | `validate_run.py:291-292` | `coverage_gate` (khác shape) | `coverage_gate.csv` | schema/template không được đọc |
| `physical_instance_ledger.csv` | template tĩnh | `physical_instance_ledger` | có | **không ai đọc** |
| `cross_view_matrix.csv`, `false_positive_ledger.csv`, `third_pass_ledger.csv` | template tĩnh | **không có** | có | **không ai đọc** |

Nguồn canonical JSON: `references/codex-execution.md:6-23`, `:36-54`. Validator chỉ đọc `.json` (`validate_run.py:22-27`); không script nào đọc `.csv` hay import `jsonschema`.

## 3. Enforced vs schema-only vs NEW

### 3.1 Enforced bởi code (existing)
- Manifest: `protocol_version` v6.4/6.4 (`:79`), `execution_contract` (`:64`), inventory doc_id/filename/page_count/sha256 (`:80-98`), `scope_pages_total` (`:104`), 2 inventory flags + 13 pass/QA flags (`:99,203-209`), `references_read` ⊇ 14 refs (`:100-103`), `memory_lookup` 3 giá trị (`:71-77`).
- Checkpoint: `stage` 4 giá trị, `next_action`, `pending_rechecks` rỗng (`:68-70`).
- Page: unique id, bộ ba khớp inventory, `status` ∈ 6, VERIFIED cần `evidence_anchors` (`:104-113`).
- Detail: `row_id`, `source_anchor`, 3 layer status, `integer_sanity_checked` cho 10 đơn vị (`:158-165`).
- Summary: `status`, `source_anchor`, `source_rows` tồn tại, zero-source reason, 3 parity flags (`:166-179`).
- Calc: prefix, formula/inputs/source_anchors/unit/raw_result, pass results, class 3 giá trị, assumption link, independent-input rule (`:180-195`).
- Assumption: unique id, evidence_class 5 giá trị, closure cho USER_CLAIM/MODEL_ASSUMPTION ảnh hưởng qty (`:144-153`).
- Issue: unique id, `final_status` 5 giá trị, evidence, Pass 3 reopen (`:243-255`).
- Pass review: `pass_no`/target/evidence/method/result, page coverage đúng, case target coverage (`:213-241`).
- Regression: đủ 23 R-*, status 3 giá trị, target/evidence/reason, MEM source (`:115-141`).
- Annotation: `issue_id/pdf_page/target_bbox/annotation_qa_status=='PASS'` (`:256-262`).
- Counters: `open_issue_count` (issue UNRESOLVED/INCONSISTENT), `open_record_count` cộng page/detail/summary/case/assumption/issue (`:139,153,255,275`).

### 3.2 Schema có nhưng KHÔNG enforced
- Toàn bộ `coverage_gate.schema.json` (`:4-8`) — validator phát object khác.
- Toàn bộ `physical_instance_ledger.schema.json` (`:4-8`) — không được load.
- `calculation_ledger.schema.json:80-106`: `status`, `rounded_result`, `rounding_rule`, `target_value_seen_before_calc`.
- `issue_ledger.schema.json:4-7,54`: `claim`, `status` (validator dùng `final_status`); không có `severity`.
- `page_ledger.schema.json:4-7`: `printed_sheet/drawing_no/revision/page_type/has_quantity_table/notes`.
- `summary_lineage_ledger.schema.json:4-8`: `reported_total/audited_total/delta/unit/name_spec/aggregation_rule`.
- `pass_review.schema.json:4-8`: `calc_id/prior_status/final_issue_state/issue_ids` (issue_ids chỉ đọc khi pass3, không bắt buộc).
- `assumption_ledger.schema.json:4-8`: `variable/status/sensitivity/source_anchor` không bắt buộc.
- `validate_run.py:196-202`: `sequence_*/discrete_*` chỉ là số tự khai, không ledger đối chiếu.
- `docs/quality-pilot-runbook.md:85`: mô tả sai stdout và version.

### 3.3 Field hoàn toàn MỚI (không schema, không validator)
- `issue.severity` (`CRITICAL/HIGH/MEDIUM/LOW/NOTE`) — annotation doc yêu cầu nhưng issue ledger/schema không có: `references/pdf-annotation-qa.md:25-37`.
- Annotation QA v4: `note_text`, `note_bbox`, `leader_start/end`, `pixel_bbox`, `transform`, `cropbox/rotation`, `dpi`, `mark_bbox`, `crop_reviewed`, status `PASS/FAIL/UNVERIFIED` — `pdf-annotation-qa.md:41-43`; validator chỉ nhận `annotation_qa_status='PASS'`.
- Receipt §4.2: `schema_version`, `run_id`, `snapshot_id`, `source_digest`, `skill_digest`, `input_digest`, `validator_exit_code`, `candidate_release_status`, `controller_status`, `validated_at` — `docs/plans/BOQ_AUDIT_QUALITY_PLAN.md:74-96`.
- `controller_status` enum PASS/BLOCKED/ERROR — `:94`.
- Path `controller/snapshots/<snapshot_id>/`, `controller/receipts/<snapshot_id>/<stage>.json` — `:67-68`.
- Work item §4.4 (`check_id`, `document_id`, `revision_digest`, `page_ids`, `target_ids`, `check_kind`, `pass_no`, `dependencies`, `applicability_reason`, `evidence_ids`, `result_status`, `execution_status`) — `:121-127`.
- Numeric check record (`formula`, `input_ids`, `units`, `tolerance_rule_id`, `actual`, `expected`) — `:141`.
- Ledger regression `no_*_reason` không có trong template nhưng validator đọc: `validate_run.py:49,154-155,199-200`.

## 4. Conflict / blocker chính

| # | Conflict | Bằng chứng |
|---|---|---|
| C1 | CSV template vs validator JSON-only | `validate_run.py:22-27`; không hit `.csv` trong scripts/tests |
| C2 | Schema không enforce; `jsonschema` không dùng | `requirements.txt`; `validate_run.py:6` |
| C3 | Lệch tên `issue_ledger`↔`issues`, `summary_lineage_ledger`↔`summary_lineage` | `inventory_pdf.py:17` |
| C4 | Schema required khác validator (`status` vs `final_status`; page thiếu `source_pdf`) | schemas vs `:104-108,250` |
| C5 | Thiếu schema/template cho detail, pass review, annotation, regression | danh sách file |
| C6 | `coverage_gate` schema/template không dùng, shape khác result | `coverage_gate.schema.json:4-8` |
| C7 | Runbook đòi v6.3 + mô tả sai stdout | `docs/quality-pilot-runbook.md:85` |
| C8 | Hash validator trong verification cũ | `docs/quality-pilot-verification.md:9` (`8bca…`) vs thực tế `86839c…` |
| C9 | `PROVENANCE.json:207-209` ghi hash SKILL.md cũ (`6174d5…`) vs đĩa `d4f1be…` |
| C10 | Backend không gọi validator; complete chỉ kiểm loại output; counters parse text | `admin.py:258-274`; `audit_runner.py:86-103` |
| C11 | Runtime trỏ skill ngoài bundle | `backend/app/core/config.py:36` |
| C12 | Upload legacy `jobs/<code>/output` vẫn thoả complete | `admin.py:186-193,264-268` |
| C13 | Physical/cross-view/FP/third-pass có template nhưng không gate | `SKILL.md:37` vs `validate_run.py:33-38` |
| C14 | `--out` tuyệt đối escape; ledger symlink không chặn | `:22-27,291-292` |
| C15 | Deliverable sibling chỉ nhận thư mục tên đúng `work` | `:269-272` |

## 5. NEW contract lead phải chốt
1. Canonical ledger format = JSON; CSV bỏ hoặc chỉ tài liệu.
2. Có enforce schema bằng `jsonschema` trong adapter Q01 hay tuyên bố documentary-only.
3. Bộ ledger bắt buộc: 12 JSON hiện đọc vs 17 ledger `data-and-delivery.md:3-20`.
4. Receipt/snapshot schema + nơi ghi controller, cấm `--out` tuỳ ý.
5. `severity` trên issue + annotation ledger v4 đầy đủ.
6. Numeric check record và helper tính lại; hiện chỉ có `sanity_checks.py:6-26`.
7. Pin digest + path interpreter; sửa C7/C8/C9.
8. Điểm chặn backend: complete/PATCH/output upload; loại legacy output khỏi publish.
9. Fixture tổng hợp only, không dùng Kênh Xáng public.

## 6. Test commands — NOT_RUN
```sh
python3 -m unittest discover -s .orchestration/skill/tests -v
python3 .orchestration/skill/scripts/validate_run.py <tmp-work> --stage pass1
python3 .orchestration/skill/scripts/validate_run.py <tmp-work> --stage pass2
python3 .orchestration/skill/scripts/validate_run.py <tmp-work> --stage pass3
python3 .orchestration/skill/scripts/validate_run.py <tmp-work>
python3 .orchestration/skill/scripts/sanity_checks.py <tmp-work>/detail_rows.json --out <tmp-work>/anomalies.json
cd backend && PYTHONPATH=. ../.venv/bin/pytest -q   # README.md:148
```

## 7. Chưa kiểm
Chưa đọc toàn bộ protocol v6.4 (chỉ phần liên quan); chưa đối chiếu §20.1/§22; chưa kiểm backend recovery/migration/race; chưa kiểm `.venv`/PyMuPDF; chưa chạy test hoặc job thật. Không đánh Q00 DONE, không bắt đầu Q01/Q08/Q10.
