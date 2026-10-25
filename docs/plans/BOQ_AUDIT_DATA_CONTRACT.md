# BOQ quality data contract v1

Ngày: 2026-10-24 (Q00b nghiệm thu 2026-10-25). Lead: Codex. Trạng thái: Q00b ACCEPTED; implementation chỉ theo contract revision này.
Phạm vi: hợp đồng adapter, snapshot/receipt và ranh giới tích hợp cho Q01–Q11. Đây không phải chứng nhận đúng khối lượng kỹ thuật.

## 1. Baseline và nguồn thẩm quyền

- App baseline: `8cdacddc3ec8187d29d20d2bc1792197abe55bdd`.
- Bundle v6.4 gồm 62 file, digest `17c14f63f78c2670428e233c4bfc56623473d1d644cf38d003db477d80c14a2c`.
- Manifest pin hiện tại là `docs/plans/BOQ_AUDIT_SKILL_MANIFEST.json`; file gốc agent `.orchestration/skill-manifest.json` có `{digest, algorithm, files:[{path, sha256}]}`. Digest là SHA256 của JSON UTF-8 của `files` với sort_keys=True, separators=(',', ':'); từng sha256 tính từ bytes thực, bỏ cache/__pycache__/.pyc. Không tin riêng hash trong PROVENANCE.json.
- Code validator tại digest này là hợp đồng compatibility hiện hành. Các JSON Schema/CSV chỉ là tài liệu nơi khác với code; adapter không tự áp cả schema cũ lên fixture validator hợp lệ.
- Yêu cầu mới ghi NEW; không diễn giải khoảng cách CURRENT→TARGET là runtime đã đáp ứng.

## 2. Mapping artifact EXISTING

Nguồn: bundle `scripts/validate_run.py:22–38,64–77,115–154,207–294`; `scripts/inventory_pdf.py`; `templates/`; `schemas/`.

| File JSON trong work/ | Kiểu | Bắt buộc từ stage | Field chính validator thực sự dùng |
|---|---|---|---|
| run_manifest.json | object | pass1 | protocol_version, execution_contract, document_inventory, inventory_verified, tables_inventory_verified, scope_pages_total, references_read, memory_lookup, memory_cases_consulted, detail_rows_total, summary_rows_total, sequence_sections_total/checked, discrete_rows_total/checked, no_*_reason khi scope zero, pass/QA flags, deliverables ở release |
| checkpoint.json | object | pass1 | stage ∈ pass1/pass2/pass3/release, next_action không rỗng, pending_rechecks=[] |
| page_ledger.json | array | pass1 | page_id unique, source_pdf, pdf_page khớp inventory; status; evidence_anchors khi VERIFIED |
| detail_rows.json | array | pass1 | row_id unique, source_anchor, spec_status/qty_status/status; unit; integer_sanity_checked cho đơn vị rời rạc |
| summary_lineage.json | array | pass1 | summary_row_id unique, status, source_anchor, source_rows, zero_source_explanation; name_spec_match/unit_match/scope_match nếu VERIFIED |
| calculation_ledger.json | array | pass1 | calc_id CALC-*, formula, inputs, source_anchors, unit, raw_result, passN_result theo stage, calculation_class, assumption_ids |
| issues.json | array | pass1 | issue_id unique; final_status bắt buộc từ pass3; evidence cho CONFIRMED_ERROR/INCONSISTENT; pass3 target; annotation_required ở release (validator-only, chưa có trong CSV/schema hiện tại) |
| regression_checks.json | array | pass1 | case_id unique, status, reason, evidence_anchors, target_ids khi CHECKED/UNRESOLVED, memory_source cho MEM-* |
| assumption_ledger.json | array | pass1 | assumption_id unique, evidence_class, affects_qty, closure_evidence |
| pass2_review.json | array | pass2 | review_id unique, pass_no=2, target, evidence, method, result, page_ids phủ toàn inventory |
| pass3_review.json | array | pass3 | như pass2 với pass_no=3, case_ids và target_ids; `issue_ids` optional nhưng mọi issue phải xuất hiện trong `target` hoặc `issue_ids`; phủ targets của case áp dụng |
| annotation_qa.json | array | release | issue_id, pdf_page, target_bbox, annotation_qa_status=PASS; đủ issue annotation_required |

Tên CSV `issue_ledger.csv` không phải `issues.json`; `summary_lineage_ledger.csv` không phải `summary_lineage.json`. Không tự chuyển CSV thành JSON hay coi CSV-only đủ gate. Giữ supplemental ledgers physical_instance/cross_view/false_positive/third_pass theo skill; validator hiện chưa gate chúng. Q03/Q05/Q07 bổ sung coverage/checker theo hợp đồng được lead duyệt, không tự báo đã kiểm.

Inventory page ID: `{doc_id}-P{pdf_page:05d}`; inventory doc gồm doc_id, filename, page_count, sha256. `sha256` hiện chỉ được validator kiểm có giá trị, không kiểm bytes hay hex; Q01 tính source digest từ nguồn được backend cấp.

## 3. Enum và giới hạn EXISTING

- REVIEWED: VERIFIED, CONFIRMED_ERROR, UNRESOLVED, INCONSISTENT, NOT_APPLICABLE, CLOSED_WITH_EXPLANATION.
- issue final: CONFIRMED_ERROR, UNRESOLVED, FALSE_POSITIVE, CLOSED_WITH_EXPLANATION, INCONSISTENT.
- regression: CHECKED, NOT_APPLICABLE, UNRESOLVED. memory_lookup: FOUND (sources không rỗng), NO_MATCH/UNAVAILABLE (reason không rỗng).
- evidence_class: OBSERVED, DERIVED, USER_CLAIM, MODEL_ASSUMPTION, RECONCILIATION.
- calculation_class: INDEPENDENT_TAKEOFF, RECONCILIATION_CHECK, SENSITIVITY_CHECK.
- R-01…R-23 tại digest này phải được xét; MEM-* được chọn thêm, không thay thế R-*.
- pending_rechecks không rỗng chặn gate. `checkpoint.stage` hiện chỉ kiểm thuộc enum, không bằng requested stage; controller mới phải kiểm stage khớp request.
- `sanity_checks.py` chỉ có cảnh báo số lượng rời rạc và STT; chưa tính tổng/nhân/đổi đơn vị độc lập. Không gọi nó là bộ kiểm số học đầy đủ.
- Pass ledger đúng metadata không chứng minh blind review. Annotation PASS không chứng minh mark trúng chữ. Deliverable hiện chỉ kiểm containment, regular file, phần mở rộng và size khác 0; không kiểm PDF/XLSX parse được.

## 4. CLI validator EXISTING và adapter NEW

Lệnh trusted argv: `[python_interpreter, pinned_bundle/scripts/validate_run.py, snapshot_work, '--stage', requested_stage]`. Không shell, không script từ work, không cho model chọn executable/--out.

| Stage | Fresh report | exit 0 hợp lệ | exit 2 hợp lệ |
|---|---|---|---|
| pass1/pass2/pass3 | stage_<stage>_result.json | METADATA_STAGE_COMPLETE | BLOCKED |
| release | coverage_result.json | READY_FOR_ENGINEER_REVIEW hoặc PARTIAL_WITH_OPEN_ITEMS_REVIEW_REQUIRED | BLOCKED |

Report đầy đủ có protocol_version=v6.4, execution_contract=checkpoint-v1, stage, gate=METADATA_ONLY, candidate_release_status, automatic_publication_allowed=false, human_engineer_signoff_required=true, open_issue_count, open_record_count, blocking_checks (array), warning. Validator kiểm tồn tại, regular file/containment, phần mở rộng và size khác 0 của deliverable; không parse nội dung Excel/PDF.
Stdout chỉ là summary, có `report` path và `blocking_checks` là ARRAY, không phải count; docs runbook cũ nói count là lỗi thời. Parse fresh report do trusted process tạo, không lấy stdout hoặc file work cũ làm receipt.

Adapter preflight input JSON trước validator; JSON input parse lỗi, duplicate key/nonfinite, missing executable/report, report JSON sai, exit không tương thích (khác 0/2), report sai stage/version/types, timeout hoặc snapshot đổi => ERROR. Validator chạy xong trả exit 2 với report `candidate_release_status=BLOCKED` => BLOCKED; missing required ledger được validator thu thành BLOCKED chứ không phải ERROR. Exit 0 chỉ hợp lệ cùng status ở bảng trên. `open_issue_count`/`open_record_count` phải int >=0, bool không chấp nhận; `blocking_checks` phải list[str]. Không log raw customer payload hoặc environment.

## 5. Snapshot và digest NEW — Q01a

- Model chỉ sở hữu work/ và output/ của attempt được giao. Controller tạo ID snapshot ngẫu nhiên, không lấy path/ID từ manifest.
- Bố trí snapshot: controller/snapshots/<id>/work/ và sibling output/. Nhờ giữ basename work, path `../output/<file>` tương thích validator. Không nới containment ra toàn jobs dir. Symlink/file đặc biệt là ERROR, không bỏ qua rồi báo PASS. Snapshot manifest liệt kê đầy đủ tập file đã chọn; file bắt buộc thiếu là BLOCKED.
- Nguồn nguồn gốc được truyền từ DB/trusted config, không theo path AI tự khai. Tên tương đối canonical, không absolute, `..`, symlink hoặc file đặc biệt; kiểm từng ancestor. Copy regular files, không follow link, phát hiện thay file khi copy; write staging rồi atomic rename cùng filesystem.
- Chỉ snapshot sau model/batch kết thúc. Kiểm fingerprint trước/sau copy và trước nhận receipt; phát hiện thay đổi phải invalidation/ERROR, không reuse file nửa chừng.
- File reports cũ `coverage_result.json`/`stage_*_result.json` không được import làm gate result. Validator chạy trên staging copy riêng và report mới không được đưa ngược vào input_digest.
- Bundle manifest ở §1 và snapshot manifest có hai dạng riêng. Snapshot manifest ordered `{path, sha256, size}`; hash bytes đã copy. `source_digest` tính nguồn do backend cấp, `input_digest` tính snapshot inputs, `skill_digest` theo §1. Output digest riêng; path+bytes đều ảnh hưởng freshness.
- Limits cấu hình trusted cho adapter: mặc định JSON 16 MiB/file, 256 MiB/output file, 512 MiB tổng snapshot, 10.000 file; timeout validator 60 giây, diagnostic tối đa 64 KiB mỗi stream. Quá giới hạn => ERROR có mã rõ; không âm thầm truncate JSON hoặc bỏ file để PASS. Đây là giới hạn kỹ thuật, không phải dung sai công trình.
- Q01 không áp dụng schema cũ thiếu tương thích. Kiểm boundary JSON shape/types/IDs mà validator cần để không crash; những kiểm mới phải có test tương ứng. Thiếu schema kỹ thuật phải hiện limitation.

## 6. Receipt và publication NEW — Q01b/Q02

Receipt schema_version=1 theo plan §4.2: run_id, snapshot_id, stage, source_digest, skill_digest, input_digest, validator_exit_code (null khi chưa có process result), candidate_release_status (null khi ERROR trước parse), blocking_checks, open_record_count (null nếu chưa tính được), validated_at UTC, controller_status PASS/BLOCKED/ERROR. Không gán count=0 khi lỗi chưa biết. Ghi receipt atomic tại controller/receipts/<snapshot_id>/<stage>.json. Trong runtime chưa enforce isolation, receipt chỉ ADVISORY dù controller_status=PASS; không được dùng để chứng nhận hay bật enforce. Unit/synthetic dùng temporary controller root tách work. Receipt trusted production cần chứng minh model không ghi được controller root, không chỉ kiểm mode bit/owner trong cùng UID.

PASS chỉ metadata hợp lệ; open_record_count không phải số lỗi unique. Không đổi JobStatus hay AuditRun.status để biểu diễn quality. `audit_quality_reviews` có DB-generated review id, unique(run_id,snapshot_id) và UNIQUE(snapshot_id) toàn cục, FK run_id tới AuditRun, stage/controller_status/candidate_release_status, quality aggregate, receipt digest(s), `output_manifest_digest`, `customer_fields_digest` và manifest đầy đủ (output_id, file_type, canonical relative path, size, sha256). Row immutable sau khi tạo; trạng thái attempt kế tiếp tạo snapshot/review mới. Quyết định signoff chỉ nằm trong event table, theo append-only. Mọi review gắn immutable snapshot; `snapshot_id` không tái sử dụng.

Append-only `audit_quality_review_events` ghi `event_id` (DB-generated autoincrement primary key), `review_id`, event_type, actor_id, reason, occurred_at. Không UPDATE/DELETE event cũ; thứ tự quyết định theo event_id, không theo clock. Current review thuộc newest run; current decision là event có event_id lớn nhất của review đó. Chỉ publish khi current review hợp lệ và current decision là `SIGNOFF_ACCEPTED`; ERROR/BLOCKED hoặc REVOKED/REJECTED không làm sống lại signoff cũ. Mọi create/signoff/revoke phải lock review/job theo cùng thứ tự; serialize event append và kiểm current state trong transaction.

Chỉ engineer reviewer được chỉ định rõ trong DB mới được ACCEPT/REJECT; nếu chưa được chỉ định, từ chối signoff. ACCEPT/REJECT phải có reason không rỗng. Allowed transitions cùng review: no event → ACCEPTED hoặc REJECTED; REJECTED → ACCEPTED hoặc REJECTED; ACCEPTED → REVOKED; REVOKED terminal. Muốn ký lại sau REVOKED phải tạo snapshot/review mới. `REVOKED` chỉ do system mutation có thẩm quyền khi current decision là ACCEPTED; event phải gắn initiating actor hoặc `SYSTEM` cùng reason/mutation id. User/admin upload, delete hoặc sửa customer-visible fields chỉ được gây revoke qua service guard, không tự append event. REVOKED/REJECTED đều vô hiệu publication hiện hành.

Upload/delete artifact hoặc sửa customer-visible notes/counters: trong cùng transaction, nếu current decision là `SIGNOFF_ACCEPTED` thì append `REVOKED`; nếu chưa có ACCEPTED hiện hành thì không tạo revoke event. Với job enforce đang `COMPLETED`, mọi mutation không tương thích phải trả job về `REVIEW` trong transaction dù không có ACCEPTED event (trạng thái thiếu chữ ký là không hợp lệ). Admin_notes không invalidate. `customer_fields_digest` tính từ canonical JSON của đúng các field hiển thị cho khách và các counter sau chuẩn hóa theo schema; không bao gồm admin_notes. Kiểm thử bắt buộc: revoke rồi upload không tái dùng signoff; hai signoff đồng thời chỉ một current; event revoke còn nguyên lịch sử; run mới chưa ký không kế thừa review cũ; timestamp trùng không đảo thứ tự event; sửa bất kỳ bound digest đều chặn publish; snapshot_id reuse giữa hai run bị unique constraint từ chối; transition REJECTED→ACCEPTED cho phép, ACCEPTED→REVOKED cho phép và REVOKED→ACCEPTED cùng review bị từ chối.

Giữ REVIEW cho draft có output; nonzero audit process không được công bố chỉ vì validator PASS. Mọi complete/output edit/status path dùng guard chung, validate ngoài transaction, sau đó recheck newest run + fingerprints trong transaction ngắn. Legacy completed giữ quyền xem, admin thấy legacy/unverified; pending và output upload không bypass.

### Quyết định khóa cho Q02

- Review có id primary key tăng dần. Current review = id lớn nhất của newest AuditRun của job, không phải review PASS mới nhất. Cấm chọn receipt cũ khi current ERROR/BLOCKED. Chọn và compare dưới cùng job transaction lock; `snapshot_id` unique toàn cục, review unique theo `(run_id,snapshot_id)`. Không cần thêm bảng publication ở MVP.
- `output_manifest` trong review lưu `output_id`, `file_type`, canonical relative path, size, sha256 cho từng `JobOutput` công bố; receipt release và signoff gắn digest manifest này. Có `ESTIMATE_REPORT` khi job có `estimate_input`; legacy/admin files phải fingerprint như attempt files. Blob được ký phải giữ bất biến trong vùng private content-addressed storage suốt vòng đời review; thay JobOutput chỉ đổi binding, không xóa blob đã ký. Job có quality review không hard-delete; dùng tombstone, việc purge lịch sử/blob cần retention policy riêng và xóa đồng bộ theo thời hạn được duyệt.
- Customer chỉ tải output ở `COMPLETED` theo policy hiện có. Với job enforce mới, download/view đọc đúng artifact bất biến gắn snapshot/review. Nếu artifact snapshot hoặc binding không kiểm được thì từ chối trả file. Legacy completed giữ đường tải hiện tại và được đánh dấu unverified cho admin.
- Upload/delete artifact: stage file mới trước, lock job, recheck newest run; thay rows và nếu có `SIGNOFF_ACCEPTED` hiện hành thì append `REVOKED`; với job enforce đang `COMPLETED`, đưa về `REVIEW` trong cùng transaction trước khi lộ nội dung mới. Delete giữ artifacts attempt lịch sử. Không thể công bố lại đến khi snapshot mới được validate và ký. Cần hành động validate/signoff rõ trong Q02, không dùng complete như bypass.
- PATCH `critical_errors`/`warnings` không được override ledger-derived counters ở enforce. Thay đổi customer-visible fields làm hết hiệu lực signoff và trả job về `REVIEW`; `admin_notes` riêng không invalidate metadata. Signoff binding gồm customer-visible notes/counters.
- `_finish`/retry/recovery tiếp tục current-run guard hiện có; mọi đổi active run/output phải invalidate quality current tương ứng. `_finish` đưa `REVIEW`/`FAILED`, không trực tiếp publish; không sửa semantics bảo toàn attempt chỉ vì thêm gate.
- Complete lặp chỉ idempotent nếu current signed snapshot/output binding còn khớp. Test upload-vs-complete, stale finalize, edit-after-signoff và thay file cùng size/mtime bắt buộc.
- Tên output giữ runner hiện tại: `BOQ_Audit_Report.xlsx`, `Annotated_Original.pdf`, `Estimate_Report.xlsx` nếu có dự toán. Manifest chỉ trỏ relative paths dưới `work/` hoặc sibling `output/`; Q07 đổi tên hiển thị theo skill phải cập nhật classifier/prompt có test, không đổi ngầm trong Q01.

Issue severity là NEW vì schema/CSV hiện chưa quy định. Extension: `severity` ∈ CRITICAL/HIGH/MEDIUM/LOW/NOTE theo palette `references/pdf-annotation-qa.md` §4; `root_issue_id` optional nhưng nếu có phải trỏ issue tồn tại và không tạo cycle. Đếm mỗi root một lần bằng khóa xác định: `root_issue_id` nếu có, nếu không `issue_id`. CONFIRMED_ERROR/INCONSISTENT là nhóm lỗi xác nhận; UNRESOLVED là nhóm riêng, không gán critical/warning khi chưa có quyết định. FALSE_POSITIVE/CLOSED_WITH_EXPLANATION không tính lỗi đang mở. Lấy severity cao nhất trong nhóm cùng lỗi. Thiếu severity, trạng thái hoặc liên kết root không hợp lệ => counts unavailable, không suy từ câu cuối AI và không tự đặt 0. Mapping hiển thị: CRITICAL/HIGH vào `critical_errors`, MEDIUM/LOW vào `warnings`, NOTE vào info count chỉ trong quality summary (không ép vào hai counter legacy); engineer quyết định severity kỹ thuật.

## 7. Coverage, lessons, evaluation NEW

- Work items/enum/dependency graph theo plan §4.4–4.5; inventory trang ≠ inventory đối tượng được duyệt. Mẫu số 0 => N/A. Global pass gate giữ nguyên, batch độc lập chỉ chạy khi prerequisite tương ứng được khóa; không đổi BLOCKED thành UNRESOLVED để lách.
- Lesson record theo plan §4.7; canonical store JSON versioned do service quản lý, SQLite chỉ index/approval metadata được rebuild từ store hoặc transaction journal rõ. Không cho JSON và DB hai nguồn sửa độc lập. Q08a bắt đầu với store thuần dữ liệu; contract tích hợp DB/API phải chốt trước Q08b.
- Candidate → VERIFIED chỉ từ reviewer được chỉ định, không chỉ vì role admin. Mọi approval ghi actor/evidence; chưa có reviewer assignment thì không approve. Version bất biến, RETIRED không được chọn run mới.
- Evaluation reference/adjudication/run record phải versioned; adjudication do người, mapping one-to-one tối đa một reference/one root finding, duplicate không tăng TP. Precision/recall mẫu số zero=null kèm N/A; failed/partial attempts không biến mất.
- Q10a phải chốt schema chi tiết với lead trước Q10b; nhãn thật/holdout không nằm trong runtime audit. Source revision mismatch không được chấm thành clean.

## 8. Isolation và chế độ được phép

Code hiện subprocess Codex cùng worker, mount ./data và codex_home; chưa chứng minh controller receipt nằm ngoài quyền model. Read-only skill mount không giải quyết điều đó.

Cho phép Q01 adapter và synthetic tests; Q02 diagnostic observe, Q05 same-context recheck nếu đúng thực tế. Chưa nghiệm thu Q02 enforce, Q05 blind hoặc Q11 live cho đến có cơ chế isolation được kiểm chứng riêng. Không dùng biến boolean tự khai thay bằng chứng isolation. Không tự mở quyền/runtime để vượt điều kiện này.

## 9. Kiểm tra và bàn giao

- Môi trường đã đọc version: `/tmp/boq-excel-testvenv-20261023/bin/python`, Python 3.14.8, pytest8.3.5/httpx2 2.13.0 và dependencies backend; CI Python3.12 còn phải kiểm riêng trước rollout.
- Backend tests phải tuần tự vì conftest.py dùng `/tmp/boq-audit-tests` và drop_all/xóa thư mục. Lead cấp lượt; không xdist. Không kết luận worktree riêng là test data riêng.
- Q00 fixtures synthetic trong backend/tests/fixtures/audit_quality; không đưa Kênh Xáng/customer files vào Git. Passing fixture chỉ chứng minh adapter tương thích metadata.
- Handoff Q00a đã lead đối chiếu với source; các điểm chưa kiểm runtime phải giữ NOT_VERIFIED. Task downstream chỉ nhận baseline/contract revision đã nghiệm thu.
