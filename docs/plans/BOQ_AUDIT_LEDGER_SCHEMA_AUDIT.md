# BOQ Audit streamed-ledger schema audit

Ngày: 2026-10-29. Phạm vi: xác định bằng chứng hiện có trước khi mở rộng controller từ minimum-field contract sang kiểm tra kiểu dữ liệu đầy đủ. Nguồn skill được pin trong worktree: digest `1fb6ea36f1727fff59d57ce45919c451cacbf24c96fcedc1b4df58cba644058b`.

## Hiện trạng

Runtime contract v3 `backend/app/schemas/audit_batch_ledger_contract.v3.json` khai báo ID field, pass được phép, required fields, known property types/enums, prefixes/ranges/array constraints và shape của `run_metadata.completed_checks`. Đối chiếu 8 schema v6.4 cho thấy mọi property và array item type đều có trong contract; các extensions controller được khai báo riêng. Type map hỗ trợ string/integer/number/boolean/object/null, nullable unions và mảng string/object; boolean không được chấp nhận thay integer/number. Đây chưa phải JSON Schema đầy đủ: field lạ ở top-level vẫn được chấp nhận và bốn loại không có schema riêng phụ thuộc protocol/reference. `calculation_ledger.inputs[]` chỉ được mô tả là object; schema nguồn không khóa keys/value bên trong nên controller chỉ kiểm kiểu object.

| Record type | Nguồn field hiện có trong skill v6.4 | Contract/runtime hiện có | Khoảng trống đã xác nhận |
|---|---|---|---|
| `page_ledger` | `schemas/page_ledger.schema.json`, `references/codex-execution.md`, `scripts/inventory_pdf.py`, `scripts/validate_run.py` | Tất cả schema fields và `source_pdf`/`evidence_anchors` extensions có type; source/page/status/evidence được kiểm | `NOT_CHECKED` là placeholder do inventory tạo và prose liệt kê; trusted release validator không tính nó là trạng thái đã rà soát. Batch output là kết quả một lần thực thi nên controller từ chối placeholder, yêu cầu trạng thái kết quả như `VERIFIED` hoặc `UNRESOLVED` |
| `detail_rows` | `references/codex-execution.md` và reference kiểm detail | Tất cả field được mô tả (`section_id`, `stt`, `unit`, quantity, sequence/integer flags) có type map; status/discrete rule được kiểm | Không có JSON Schema; chưa có allowlist authoritative cho các field khác. `stt` chấp nhận string/integer |
| `summary_lineage` | `schemas/summary_lineage_ledger.schema.json`, execution reference | Mọi schema property và `source_anchor`/`zero_source_explanation` extensions có type; source rows/reconciliation được kiểm | CSV template có `summary_row`/`source_pages`, nhưng không nằm trong streamed JSON schema/execution contract; chưa đưa vào runtime payload |
| `calculation_ledger` | `schemas/calculation_ledger.schema.json`, execution reference | Mọi schema property/item type được map; ID prefix, numeric result, object-array inputs, anchors/class được kiểm | Schema/controller yêu cầu object array nhưng hai test legacy bundle dùng map/scalar; input object member shape không được schema mô tả |
| `assumption_ledger` | `schemas/assumption_ledger.schema.json`, execution reference | Mọi schema properties/type được map; evidence class, ASM prefix và closure rule được kiểm | `value` là JSON tự do; không có domain type để siết |
| `issue_candidates` | `references/codex-execution.md`, `references/three-pass-protocol.md`; gần nhất là issue schema nhưng ID/closure khác | Required claim/evidence IDs; evidence phải cùng batch | Không có schema riêng; không được áp issue schema nguyên dạng vì candidate chưa phải finalized issue |
| `regression_checks` | `references/codex-execution.md`, `references/regression.md`, `references/protocol-v6.4.md` | Required status/anchors/reason; kiểm trạng thái, MEM source và target IDs theo status | Không có JSON schema; cần khóa cấu trúc R-* và MEM-* cùng lý do N/A/UNRESOLVED theo tài liệu |
| `physical_instance_ledger` | `schemas/physical_instance_ledger.schema.json`, execution reference | Tất cả schema fields/type được map; view evidence phải không rỗng | Không có khoảng trống type đã xác nhận; correctness instance/view vẫn cần reviewer |
| `run_metadata` | `references/codex-execution.md`, controller-batch protocol | Required stage/references/memory/completion; đúng một record ở Pass 1; kiểm ID, path, R/MEM ID, completion flags/evidence | Không có schema; completion check hiện có contract riêng và cần được ghi thành JSON Schema tương đương |
| `pass2_review` | `schemas/pass_review.schema.json`, execution reference | Mọi schema fields/item types; page IDs nonempty/unique, pass=2 và evidence scope được kiểm | Coverage union là gate riêng; schema không chứng minh nội dung review |
| `pass3_review` | `schemas/pass_review.schema.json`, execution reference/protocol-v6.4 | Mọi schema fields/item types; arrays unique, pass=3 và issue/case closure được kiểm | Schema chung không biểu đạt toàn bộ closure liên record; giữ domain checks riêng |
| `issues` | `schemas/issue_ledger.schema.json`, execution reference | Mọi schema fields/type + `candidate_ids`; final enum, pass_found 1..3 và evidence được kiểm | Candidate linkage là controller extension; kỹ sư vẫn phải xác nhận nội dung/closure |

Conflict found inside the pinned skill bundle for `calculation_ledger.inputs`: the JSON schema declares a non-empty-capable array of objects, and the controller batch protocol/runtime require a nonempty array of objects; however, legacy `tests/test_workflow.py` uses an object map and `tests/test_scripts.py` uses an array of scalars. The bundled `validate_run.py` only checks that `inputs` is truthy, so it does not resolve the shape conflict. Contract v3 follows the schema plus batch protocol (`object[]`); a compatibility regression now accepts `[{"name":"n","value":2}]` and rejects the two conflicting legacy shapes. This is an explicit lead decision for new controller output, not proof that all older skill examples/tests are current. The v6.4 bundle should reconcile those legacy fixtures before claiming end-to-end skill compatibility.

## Quyết định triển khai

Không thêm `additionalProperties: false` cho đến khi inventory mọi field hợp lệ (gồm field mới của v6.4) được đối chiếu với prompt, schemas, reference và caller. Việc này tránh làm hỏng output hợp lệ chỉ vì schema gốc thiếu extension đã được skill mô tả. JSON Schema không thay thế controller checks về source binding, batch evidence, pass isolation hoặc closure liên record.

Task còn lại để hoàn thành schema/compatibility validation:

1. Kiểm tra nested values còn tự do (đặc biệt `calculation_ledger.inputs`) và protocol-only records; chỉ siết khi nguồn pin định nghĩa cấu trúc.
2. Đối chiếu compatibility fixtures với output Codex thật/corpus adjudicated trước khi cân nhắc strict top-level allowlist. Không sửa ledger đã persisted.
3. Giữ ranh giới trạng thái: `NOT_CHECKED` hợp lệ trong inventory/checkpoint khởi tạo, nhưng không phải kết quả domain ledger của một batch đã thực thi. Test `test_unchecked_page_state_is_rejected_before_pass_gate` khóa quyết định này; nếu protocol muốn cho phép placeholder trong batch output, cần thay đổi contract và gate cùng lúc.
4. Chạy focused ledger/checkpoint/runner/report tests rồi full backend Python 3.12 sau mỗi thay đổi contract. Live Codex/VM output conformance là gate riêng, không suy ra từ synthetic fixtures.

## Bằng chứng và giới hạn

- Contract/runtime: `backend/app/schemas/audit_batch_ledger_contract.v3.json`, `backend/app/services/audit_batch_ledgers.py`.
- Source skill: `schemas/*.schema.json`, `references/codex-execution.md`, `references/controller-batch-protocol.md`, `references/protocol-v6.4.md`.
- Tests hiện có: `backend/tests/test_audit_batch_ledgers.py`, `test_audit_batch_outputs.py`, `test_audit_checkpoint.py`, `test_audit_runner_batch.py`.
- Bộ test chứng minh các case synthetic đã khai báo; chưa có Codex output thật hoặc corpus audit đã adjudicate để chứng minh compatibility của strict schema.

## Compatibility correction — 2026-10-30

Resolved the pinned-skill fixture conflict above without changing the `object[]` contract in `schemas/calculation_ledger.schema.json` or contract v3:

- `tests/test_workflow.py` now uses `inputs=[{"name":"n","value":1}]` (was the map `{"n":1}`).
- `tests/test_scripts.py` now uses `inputs=[{"name":"a","value":1}]` (was the scalar array `[1]`).
- Added `test_calculation_inputs_follow_schema_object_array` in the bundled workflow tests: asserts the schema is `array` of `object`, the fixture is a non-empty list of objects, and the release gate still returns `READY_FOR_ENGINEER_REVIEW`.
- Added `backend/tests/test_skill_fixture_compatibility.py`: AST-checks both bundled fixture literals are non-empty object arrays, feeds each into `validate_batch_ledger_records` (accepted), keeps rejecting map/scalar/empty inputs, and runs the pinned `validate_run.py` workflow test in a subprocess.

Artifacts were rebuilt in dependency order: `PACKAGE_MANIFEST.txt` from the current tree, `PROVENANCE.json` `installed_files`/`local_overlay` hashes (original archive hashes in `files` untouched), both pinned manifests (63 files) and the archive. New trusted digest: `cd1101d79b3edd1311d2b60942d30a80bedac4027856bf798ceaadb36cdebf5b`; `scripts/prepare_pinned_skill.py pack|verify` both report 63 files. Skill tests: 26 passed. Focused backend (fixture compatibility, batch ledgers, quality/pin, runner quality, stage gate): 153 passed. Full backend: 494 passed. The VM host skill was not touched; live Codex output and adjudicated corpus compatibility remain gates.

## Implementation update — 2026-10-29

Contract v3 giữ type/enum và nested shape validation từ v2, đồng thời khai báo schema-derived constraints: prefix `ASM-`, `pass_found` trong 1..3, unique arrays cho pass review và tối thiểu một `page_id`. Generic validator áp prefix/range/min/unique constraints trước domain checks. Regression v3 thêm các case assumption ID, pass ngoài phạm vi, duplicate IDs và mảng trang rỗng; test riêng khóa việc từ chối page `NOT_CHECKED` trong batch output. Focused suite: 75 passed; full backend ngoài sandbox: 435 passed, 2 deprecation warnings; evaluator/corpus: 14 passed. Full run trong sandbox timeout 300s tại 82% (exit 124); cùng suite ngoài sandbox hoàn tất. Đây chưa phải full JSON Schema/strict top-level field allowlist.
