# Kế hoạch nâng chất lượng BOQ Audit và tái sử dụng kinh nghiệm

Ngày: 23/10/2026. Chủ yêu cầu: Thang. Trạng thái: PLAN ONLY — chưa có task nào được triển khai theo kế hoạch này.
Mục tiêu: có thể giao từng task cho agent OpenCode với phạm vi hẹp, hợp đồng rõ và nghiệm thu bằng bằng chứng.
Nguồn: [khảo sát](../REVIEW_2026-10-23.md), [quality pilot](../designs/boq-audit-quality-pilot.md), [recovery](../AUDIT_RECOVERY_PLAN.md), skill v6.4 đang cài.

## 1. Kết quả cần đạt

- Biết chính xác trang, dòng, cấu kiện và phép kiểm nào đã làm, chưa làm, thiếu dữ liệu hoặc không áp dụng.
- Backend tự kiểm chứng metadata và tính nhất quán; không tin cờ PASS hoặc câu kết luận do model tự ghi.
- Không bỏ pass 2–3 chỉ vì một phần dữ liệu bị thiếu; không đánh dấu các pass này hoàn tất khi prerequisite chưa đủ.
- Giữ kết quả đã xác minh sau gián đoạn, nhưng kiểm lại phần bị ảnh hưởng khi dữ liệu/giả thiết thay đổi.
- Mỗi lỗi bỏ sót đã xác nhận trở thành ví dụ hồi quy; bài học được duyệt trở thành câu hỏi kiểm tra áp dụng cho hồ sơ mới.
- Báo cáo Excel/PDF, số lỗi và UI cùng xuất phát từ ledger hiện hành.
- Đo được lỗi tìm đúng, lỗi bỏ sót, báo sai và phạm vi chưa kết luận. Không biến tỷ lệ coverage metadata thành độ chính xác kỹ thuật.

Không nằm trong đợt này: thay model audit đại trà, vector database, fine-tuning, đổi SQLite, tăng số audit worker, thiết kế lại toàn bộ UI, tra cứu luật, thay cơ chế đăng nhập. Kiểm chứng isolation là điều kiện chạy pilot sống, không mở rộng thành dự án bảo mật toàn hệ thống ở đây.

## 2. Bằng chứng hiện có và điều phải xác minh trước code

Runner hiện gọi Codex một lần cho cả audit; `_finish()` kiểm required output types rồi đưa REVIEW, admin mới công bố. Worker đã quản lý attempt, khóa tiến trình và recovery. Skill đã có `checkpoint-v1`, JSON/CSV ledger, regression cases và validator. Đừng xây lại các phần đã có.

Skill không nằm trong Git repo ứng dụng: đường dẫn config `boq_audit_skill_path`, mặc định container `/opt/codex/skills/boq-audit/SKILL.md`; local `/home/thangpham/.agents/skills/boq-audit/SKILL.md`. Sửa skill là thay đổi artifact riêng, không coi commit ứng dụng đã mang theo skill.

Tên test thực tế gồm `backend/tests/test_recovery.py`, `test_jobs.py`, `test_timing_chat.py`; không có `test_audit_runner.py` ở mốc khảo sát. CI backend cài `backend/requirements-dev.txt`, chạy `PYTHONPATH=. pytest -q` trong backend. Lệnh host có thể thiếu pytest: dùng venv riêng, không báo pass khi chưa chạy.

## 3. Quy tắc cho người điều phối và agent

1. Mỗi task một branch/checkout sạch từ baseline do người điều phối chốt. Không làm trên workspace đang dirty, không reset/clean/stash thay đổi của người dùng.
2. Agent chỉ nhận một task card và các hợp đồng cần thiết. Không giao yêu cầu chung kiểu “làm audit thông minh hơn”.
3. Agent viết phần mềm được đặc tả; không tự quyết kỹ thuật cầu, dung sai, nhãn reference, mức độ nghiêm trọng hay applicability không có bằng chứng.
4. Agent không được nới gate để fixture pass, hard-code known issue, bỏ test fail hoặc ghi PASS giả. Thiếu dữ liệu ghi blocker cụ thể.
5. Task downstream chỉ bắt đầu sau khi dependency đã nghiệm thu/merge. Không nhiều agent cùng sửa runner/model/schema/API trong một lượt.
6. Chỉ reviewer cấp cao được thay hợp đồng hoặc chọn cách xử lý mâu thuẫn. Agent nêu vấn đề trong handoff, tiếp tục phần độc lập.
7. Không commit báo cáo khách hàng, auth, session, raw log hoặc file nguồn. Fixtures tổng hợp không dùng dữ liệu thật chưa được phép.
8. Chỉ lead được merge/deploy sau các cổng cuối. Agent OpenCode không được tự gửi dữ liệu dự án tới provider ngoài phạm vi được cấp.

## 4. Kiến trúc đích và ranh giới tin cậy

```text
Nguồn bất biến + skill phiên bản cố định + bài học đã duyệt
                   |
           inventory / work plan
                   |
        batch pass1 -> batch pass2 -> batch pass3
                   |                    |
              ledger patches      correction/rechecks
                   |                    |
          backend validate + snapshot/hash
                   |
       ledger hiện hành -> Excel/PDF -> QA -> admin review
                   |
         phản hồi -> candidate lesson -> chuyên gia duyệt
                   |
       kho bài học versioned + bộ ví dụ đánh giá riêng
```

Model tạo dữ liệu dự thảo. Controller sở hữu trạng thái thực thi và bằng chứng validator. Không để model ghi vào nơi backend lưu kết quả gate đáng tin cậy. Hash chỉ chứng minh bytes không đổi; không chứng minh nội dung đúng hoặc đọc nguồn thật.

### 4.1 Bố trí artifact

Giữ `jobs/<code>/attempts/<attempt>/` hiện có. Dùng:

- `work/`: manifest, checkpoint và ledger theo đúng tên/format của skill. Q00 phải khóa mapping thực tế trước code.
- `output/`: giữ tên báo cáo hiện có.
- `controller/`: controller-owned snapshots, receipts và batch result metadata. Chỉ controller được ghi; không mount writable thư mục này vào model. Nếu runtime chưa cưỡng chế được, chưa được coi receipt là đáng tin và chưa bật chế độ enforce.
- `controller/snapshots/<snapshot_id>/`: bản sao ledger/manifest đã đóng, source fingerprint, skill digest, output digest khi release. Reject symlink/traversal; dữ liệu phải nằm trong attempt được phép.
- `controller/receipts/<snapshot_id>/<stage>.json`: kết quả backend chạy trusted validator trên snapshot bất biến. Validator được phép ghi output của nó trong snapshot kiểm tra tạm; receipt cuối ghi ngoài quyền model.

Không đọc script validator do model tạo. Chạy bằng argv, executable/interpreter được cấu hình, không shell interpolation. Script từ bundle tin cậy đã cố định digest, không theo path nằm trong manifest do AI viết.

### 4.2 Receipt do backend tạo

Hợp đồng mới, lưu JSON versioned:

```json
{
  "schema_version": 1,
  "run_id": 42,
  "snapshot_id": "backend-generated-id",
  "stage": "pass1",
  "source_digest": "sha256",
  "skill_digest": "sha256",
  "input_digest": "sha256",
  "validator_exit_code": 0,
  "candidate_release_status": "METADATA_STAGE_COMPLETE",
  "blocking_checks": [],
  "open_record_count": 0,
  "validated_at": "UTC timestamp",
  "controller_status": "PASS"
}
```

`controller_status`: PASS / BLOCKED / ERROR. PASS là metadata pass. Timeout, script missing, JSON invalid, mismatch stage/version hoặc exit code không tương thích => ERROR; không suy PASS từ file cũ. Stage/release status giữ đúng vocabulary validator hiện có.

`open_record_count` đếm record mở, có thể trùng một vấn đề ở nhiều ledger; không dùng nó làm số lỗi duy nhất. Critical/warning lấy issue ledger đã de-duplicate và unresolved theo hợp đồng Q00; không parse câu cuối của model làm nguồn chính.

### 4.3 Trạng thái ứng dụng và công bố

Giữ `JobStatus` và lifecycle `AuditRun` hiện tại. Không thêm enum tùy ý để đại diện chất lượng.

Thêm bảng riêng `audit_quality_reviews` qua pattern `Base.metadata.create_all` hiện có, tránh thêm column cũ mà quên migration. Một review gắn `run_id` + `snapshot_id`; lưu receipt digests, chất lượng aggregate, quyết định signoff, actor, thời điểm, lý do và output digests. Unique constraints phải được khóa trong Q00. Receipt chỉ cho admin; API customer chỉ nhận phần tóm tắt công bố.

| Trường hợp | Lifecycle / hành động |
|---|---|
| Timeout/không có returncode hoặc thiếu output bắt buộc | Giữ chính sách FAILED hiện có; giữ artifacts |
| Process exit khác 0 nhưng có đủ output | Hiện tại runner đưa REVIEW; kế hoạch giữ draft REVIEW, thêm quality ERROR và chặn complete cho tới khi được chạy/kiểm lại hợp lệ |
| Có output nhưng gate BLOCKED/ERROR | Cho admin xem draft qua REVIEW với nhãn chưa đạt; endpoint complete trả 409 |
| Metadata đầy đủ, còn unresolved | REVIEW, hiển thị PARTIAL; chỉ công bố báo cáo PARTIAL khi kỹ sư ký rõ giới hạn và không còn lỗi quy trình chặn |
| Metadata đầy đủ, không mục mở | REVIEW; vẫn cần người kỹ thuật duyệt |
| Receipt stale hoặc output đổi sau signoff | Từ chối publish; validate và ký lại |
| Job COMPLETED từ trước migration | Giữ quyền xem báo cáo cũ, ghi legacy/unverified khi admin xem; không tạo PASS hồi tố |
| Job cũ chưa công bố / báo cáo admin upload | Phải được migrate checklist và kiểm lại; không cho bypass gate qua endpoint khác |

Gate cuối và signoff phải gắn cùng snapshot + output digests. Serialize signoff/complete/output replacement với current attempt bằng transaction ngắn; không giữ transaction khi chạy validator. Sau khi validate xong, kiểm tra fingerprint và newest run lại trong transaction trước commit.

### 4.4 Coverage và BLOCKED

Inventory có hai lớp: danh sách trang do parser tạo và danh sách đối tượng/dòng/check cần rà do trích xuất + reviewer xác nhận. Trang phủ đủ không chứng minh mọi đối tượng đã được nhận diện. UI phải thể hiện giới hạn inventory đối tượng chưa duyệt.

Work item ổn định: `check_id`, `document_id`, `revision_digest`, `page_ids`, `target_ids`, `check_kind`, `pass_no`, `dependencies`, `applicability_reason`, `evidence_ids`, `result_status`, `execution_status`.

- `execution_status`: PENDING / RUNNING / DONE / BLOCKED / INVALIDATED.
- `result_status`: NOT_CHECKED / CHECKED / UNRESOLVED / NOT_APPLICABLE.
- NOT_APPLICABLE cần lý do và evidence; không loại khỏi mẫu số âm thầm.
- Báo số riêng: tổng trong scope, checked, unresolved, chưa kiểm, không áp dụng. Không cho 0/0 thành 100%.
- DONE + UNRESOLVED nghĩa đã làm nhưng chưa kết luận, không phải checked clear.

Mâu thuẫn cần xử lý rõ: skill hiện chặn chuyển stage khi gate BLOCKED. Kế hoạch giữ nguyên global gate; scheduler vẫn làm phần độc lập trong stage hiện tại. Chỉ khi bổ sung và duyệt hợp đồng per-work-item prerequisite mới cho batch pass2/3 của phần đủ điều kiện chạy sớm. Kết quả đó là draft, không set global pass2/3 complete. Phần thiếu bằng chứng kỹ thuật có thể UNRESOLVED mà metadata hợp lệ; khi ấy tiếp tục stage tiếp theo theo skill hiện tại. Không đổi mọi BLOCKED thành UNRESOLVED để lách gate.

### 4.5 Tái sử dụng và correction

Dependency IDs nối source anchor -> observed value -> CALC -> detail -> summary -> BOQ/estimate -> issue -> report/annotation. Mỗi node lưu source revision và input digest. Correction trước hết là claim chưa xác minh; sau mở nguồn xác nhận mới cập nhật dữ liệu.

Mọi sửa nguồn hoặc correction đã xác nhận phải đánh INVALIDATED toàn bộ downstream trong cùng một transaction/state update trước khi tính lại. Giữ lịch sử, thu hồi annotation sai và signoff cũ. Dependency thiếu => vô hiệu hóa toàn assembly hoặc toàn run nếu chưa xác định được phạm vi; không giữ màu xanh dựa vào phỏng đoán.

Sau restart vẫn tạo attempt mới theo recovery hiện có. Import các snapshot batch hợp lệ của attempt trước với provenance `derived_from_run_id`; không sửa attempt cũ. Chỉ reuse khi source digest, skill digest, checker/config phiên bản và dependencies khớp. Khi không chứng minh được khớp, chạy lại. Không giữ thread model cũ làm bằng chứng đã kiểm.

### 4.6 Kiểm số học bằng code

Tái sử dụng `scripts/sanity_checks.py` và các phép kiểm có sẵn của bundle; Q00 lập danh sách capabilities thực tế. Những phép kiểm đã có đầu vào và công thức xác định (cộng dòng/tổng, nhân số lượng, đổi đơn vị được khai báo, trùng physical-instance ID, thiếu liên kết detail-summary) phải được code tính lại thay vì chỉ hỏi model lần nữa. Kết quả lưu formula, input IDs, units, tolerance rule ID và actual/expected. Không dùng `eval` để chạy công thức từ tài liệu. Dung sai và công thức kỹ thuật phải do protocol/reviewer chốt; thiếu kích thước không tự suy hình học để khớp BOQ. Q05 triển khai và test wiring các phép kiểm đã được duyệt; chưa hỗ trợ phép kiểm nào thì ghi limitation, không coi giá trị AI tự khai là kết quả tính độc lập.

### 4.7 Kho bài học

MVP dùng metadata JSON versioned ở vùng dữ liệu ứng dụng, cùng index/approval trong SQLite nếu cần API review; không đưa case của khách hàng vào Git. Agent Q08 phải chốt một nguồn truth, không để JSON và DB cùng được sửa độc lập.

Record: `lesson_id`, `version`, `status`, `error_class`, `trigger_conditions`, `required_checks`, `counterexamples`, `source_case_ids`, `evidence_refs`, `reviewer_id`, `reviewed_at`, `supersedes`, `project_scope`, `visibility_scope`.

Lifecycle: CANDIDATE -> VERIFIED -> RETIRED/REJECTED. Chỉ kỹ sư/admin được chỉ định có thể VERIFIED; ghi actor thực. AI không có quyền duyệt. Chưa có reviewer đủ năng lực thì candidate vẫn chờ; không phong danh hiệu chuyên gia dựa vào role admin.

Select bằng tag/quy tắc xác định được trước; không cần embedding. Run ghi version cụ thể và disposition cho từng lesson được cân nhắc. Bài học dùng chung phải được khử dữ liệu riêng và duyệt quyền chia sẻ. Khi memory UNAVAILABLE/NO_MATCH, vẫn kiểm bộ R-* trong skill và ghi giới hạn.

### 4.8 Đánh giá chất lượng

Tái dùng pilot/runbook/worksheet trong `docs/quality-pilot-*`. Đáp án chuyên gia phải tách khỏi filesystem/model runtime của audit, không chỉ dặn model “đừng đọc”. Chạy evaluation tổng hợp trước; file thật chỉ khi quyền dùng, confidentiality và isolation được xác nhận.

Mỗi finding reference có object/source anchor/error class/numerical basis/tolerance/severity và reviewer. Ghép một lỗi gốc tối đa một reference; kết quả mơ hồ do người duyệt xử lý. Không tự dùng LLM làm trọng tài duy nhất.

Metrics: TP, FP, FN, unresolved, unscored, failed/partial attempts; recall=TP/(TP+FN), precision=TP/(TP+FP), mẫu số 0 => N/A. Phân nhóm severity/error class/case và tách lỗi cấy nhân tạo khỏi lỗi thực. Lỗi mới được phát hiện phải adjudicate/version nhãn và giữ kết quả baseline cũ. Failed attempt không được biến mất khỏi thống kê hoặc tự coi là full coverage.

Tiêu chí pilot: phát hiện mọi critical reference trong bộ đánh giá với evidence, không có critical false positive đã xác nhận, không có all-clear thiếu căn cứ, mọi finding có nguồn kiểm được. Đây không phải chứng nhận cho mọi hồ sơ.

## 5. Danh sách task giao agent

Mỗi task dưới đây là một lần giao việc. Chỉ tạo file/module mới nếu chức năng không có sẵn; tên đề xuất là hợp đồng cho đợt này. Các task phải bổ sung test riêng có ý nghĩa, không test chỉ assert constant.

### Q00 — Khóa baseline và hợp đồng dữ liệu

Owner: lead + agent khảo sát read-only. Dependency: không.
Đọc: runner, models, schemas, admin complete, recovery tests; toàn bộ skill templates/schemas và validator thực tế; docs pilot/recovery.
Làm: tạo `docs/plans/BOQ_AUDIT_DATA_CONTRACT.md`, ghi SHA repo, dirty diff phạm vi, skill digest; mapping từng tên ledger/CSV/JSON, khóa enum/key và schema examples. Chỉ rõ trường agent ghi, controller ghi, version compatibility và paths.
Phải xác minh: bundle validator đọc JSON hay CSV ở mỗi bảng, policy output path vì `work/` và `output/` là sibling; không sửa containment rộng để cho bất kỳ path nào đi qua. Chốt mapping theo bundle đang chạy trước mọi implementation.
Đầu ra: data contract + fixtures tổng hợp tối thiểu theo đúng bundle, danh sách regression R-* tại digest đó; không dùng bản Kênh Xáng làm fixture public.
Nghiệm thu: từng field trong contract truy về code/schema thật hoặc được đánh NEW; mọi conflict giữa docs và code có quyết định lead. Không thay runtime.

### Q01 — Adapter validator độc lập

Dependency: Q00. Files: mới `backend/app/services/audit_quality.py`, `backend/tests/test_audit_quality.py`; config tối thiểu nếu cần timeout.
Làm: xác thực path/size; snapshot atomic khi model đã kết thúc batch; invoke trusted validator bằng subprocess argv, timeout và bounded logs; parse JSON/status/exit code; lưu receipt controller-owned. Compute digest từ danh sách path+bytes đã canonicalize; không tin manifest tự khai hash.
Tests: metadata hợp lệ, thiếu ledger, JSON lỗi, timeout, exit2, exit lạ, stage mismatch, path traversal/symlink, giả mạo gate file, file sửa giữa snapshot/validation. Script fake chỉ dùng để test wrapper; thêm fixture chạy validator bundle thật khi bundle đã được pin.
Nghiệm thu: kết quả fake PASS trong work không thể tạo receipt PASS; input thay đổi làm receipt stale; không log dữ liệu/secret. Gate chỉ ghi metadata status.

### Q02 — Chặn công bố khi chưa đạt chất lượng

Dependency: Q01. Files: entities, migrations theo pattern hiện có, schemas/api, api/admin, audit_runner; tests recovery/jobs + test_audit_quality.
Làm: bảng `audit_quality_reviews`; chạy adapter ngoài DB transaction sau process/batch xong; lưu quality snapshot trong finalization guarded by current run; kiểm ở mọi đường complete/status/output edit. Lấy counters từ ledger theo Q00. Giữ REVIEW cho draft để admin nhìn lỗi gate.
Tests: complete/PATCH COMPLETED đều chặn blocked; partial đòi explicit signoff; receipt stale, output thay sau ký, stale attempt, race validate-vs-edit, legacy completed vẫn đọc được; migration chạy hai lần không mất dữ liệu.
Nghiệm thu: không đường API nào publish được artifact chưa kiểm hiện hành; không mất lịch sử retry; không thay customer ownership semantics.

### Q03 — Coverage inventory và trang tiến độ tối thiểu

Dependency: Q02. Files: mới services/audit_coverage.py; api/admin, schemas/api; frontend/types và trang admin job hiện có; tests coverage/API.
Làm: import inventory nguồn + work items theo contract; reject duplicate/unknown IDs, sai revision/page, thiếu phạm vi. Tính counts trong backend; thêm admin API `/api/admin/jobs/{job_code}/quality` (xác minh prefix router trước code). UI hiển thị pass, coverage, unresolved, blockers, gate freshness và link artifacts được phép.
Tests: duplicate page/target, missing page, 0 denominator, N/A không lý do, unresolved không tính checked clear; customer không đọc raw evidence của job khác; progress tính từ backend không từ đoạn text model.
Nghiệm thu: với fixture có một trang bỏ sót, UI hiện đúng mục thiếu và complete bị chặn. Trang không có bảng vẫn có work item kiểm bản vẽ/spec; inventory object chưa xác nhận phải hiện giới hạn.

### Q04 — Controller thực thi theo batch, giới hạn sửa và retry

Dependency: Q03; lead duyệt revision hợp đồng skill cho batch trước code.
Files: mới services/audit_workflow.py, runner/worker tích hợp; skill execution-workflow/templates ở bundle riêng; tests workflow/recovery.
Làm: controller chọn batch hữu hạn từ inventory, cấp phạm vi/nguồn/checklist, nhận ledger patch; validate schema và scope rồi merge atomically. Chỉ một writer áp dụng patch; patch không được sửa target ngoài batch. Mỗi lần invoke có stage timeout và tổng budget attempt.
Cơ chế repair: tối đa 2 lượt sửa cho cùng batch và fingerprint blocker; giữ checkpoint, sau đó đánh BLOCKED và tiếp tục phần độc lập được phép. Không vòng lặp vô hạn hoặc tự nới phạm vi. Giữ chính sách global gate ở §4.4.
Tests: process chết trước/giữa commit, duplicate patch/idempotency, batch thiếu/ngoài scope, timeout, repeated blocker, source đổi, tổng budget hết, independent work vẫn tiến triển; không đánh global pass2 complete khi pass1 bị chặn.
Nghiệm thu: simulation có batch A thiếu dữ liệu, B hợp lệ; B hoàn tất, A hiện blocker, không báo toàn run xong. Không cần gọi model sống để chứng minh scheduler.

### Q05 — Pass 2 và pass 3 có evidence riêng

Dependency: Q04. Files: workflow/prompt builders; skill protocol adapters; tests phase payload/coverage.
Làm: pass2 dựng scope từ inventory, ưu tiên trang không issue và dòng đã “xanh”; kết quả riêng trước reconcile. Blind reviewer chỉ nhận nguồn/scope/quy tắc, không ledger đáp án pass1, không workspace chứa đáp án. Nếu không có isolation này, ghi same-context recheck, tuyệt đối không gắn independent/blind.
Pass3 đọc từng issue và case áp dụng, kiểm target coverage, micro-details và bác bỏ; lỗi đóng phải có reason/source và cập nhật downstream. Không dùng một phép tính mẫu đại diện mọi target.
Tests: sentinel đáp án pass1 không xuất hiện trong payload hoặc mount pass2; pass3 thiếu một issue/target bị chặn; duplicate view không tạo independent evidence; copy output giữa pass không được controller tự coi là bằng chứng độc lập.
Nghiệm thu: có thể truy mỗi kết luận về evidence riêng; reviewer kỹ thuật xác nhận quy tắc pass phù hợp trước live pilot. Tests chỉ chứng minh wiring, không chứng minh model thực sự đọc/hiểu đúng.

### Q06 — Dependency invalidation và tiếp tục sau gián đoạn

Dependency: Q05. Files: mới services/audit_dependencies.py; workflow/recovery; tests dependencies/recovery.
Làm: adjacency list IDs theo §4.5, cycle detection, missing-node handling; correction đánh invalidation trước recalculation. Recovery import snapshot hợp lệ sang attempt mới, lưu provenance; so source/skill/checker/config digest và dependent hashes.
Tests: sửa A làm CALC/summary/BOQ/issue/annotation phụ thuộc stale; phần B không phụ thuộc giữ được; thiếu dependency mở lại assembly; cycle chặn; skill/source đổi không reuse; crash không tạo partial snapshot hợp lệ; attempt cũ bất biến.
Nghiệm thu: không còn report/annotation xanh dựa trên source đã sửa; resume không làm thay đổi current-run guard và retry policy hiện có.

### Q07 — Bằng chứng và báo cáo thống nhất

Dependency: Q06. Files: module exporter/QA trong skill bundle (Q00 chỉ rõ vị trí thật); service quality, API output/signoff nếu cần; tests report contracts.
Làm: source anchors gồm document/revision/page/object, bbox đúng hệ tọa độ và rotation; Excel anchor sheet/cell/range. Issue ID/CALC ID ổn định; snapshot ledger là nguồn xuất Excel/PDF/counters. Giữ đầy đủ sheet, font/màu severity và kỹ thuật annotation v4 theo skill hiện hành.
Kiểm tự động: ID references, source bounds, sheet bắt buộc, issue counts, severity map, dangling annotations. Kiểm người: crop từng annotation và mẫu nguồn tính toán; bounds đúng không chứng minh khoanh trúng chữ.
Tests: thiếu sheet, lệch count, orphan ID, bbox ngoài trang/rotation, issue đã bác bỏ vẫn còn mark, output bị thay sau QA. Không tự tạo format Excel mới.
Nghiệm thu: một issue chỉnh hoặc đóng phải cập nhật đồng bộ ledger, Excel, PDF, counters và signoff; mọi mark có QA record trên render cuối.

### Q08 — Kho bài học có kiểm duyệt

Dependency: Q00; chỉ tích hợp sau Q06. Files: mới services/audit_lessons.py, models/schema và admin API nhỏ; tests lessons/authorization.
Làm: implement lifecycle §4.7; candidate từ correction kèm source provenance, approval do người; versions immutable, retire/supersede. API validate path/ownership/content size; export sanitized shared rules chỉ khi được duyệt.
Tests: AI/customer không approve; candidate không vào verified list; chỉnh lesson tạo version mới; retired không chọn cho run mới; bài học private không rò sang project/user khác; không có reviewer thì không tự approve.
Nghiệm thu: có thể truy lý do VERIFIED và evidence; một bài học sai có thể bị thu hồi mà lịch sử run vẫn giữ version cũ. Không import raw chat history tự động.

### Q09 — Nạp bài học và bắt buộc kiểm đủ target

Dependency: Q08 + Q06. Files: lessons selection, workflow initialization, skill regression adapter; tests applicability.
Làm: kết hợp R-* tại skill digest với MEM-* đã duyệt phù hợp scope; deterministic selection theo tags. Snapshot selection/disposition; checklist target IDs đầy đủ; kiểm lại nguồn mới. Chưa rõ applicability => UNRESOLVED, không âm thầm loại. Pass3 kiểm lại toàn bộ target áp dụng.
Tests: không có memory vẫn có R-*; unavailable ghi lý do; nhiều target chỉ kiểm một không qua gate; lesson đổi giữa run không đổi snapshot; không copy giá trị số ví dụ thành kết luận hồ sơ mới.
Nghiệm thu: admin thấy bài học nào đã dùng, không dùng, còn thiếu và lý do; kiểm được omission có chủ ý trong fixture.

### Q10 — Bộ đánh giá hồi quy độc lập

Dependency: Q07 + Q09 cho pipeline mới; có thể chuẩn bị nhãn sớm offline.
Files: mới `evaluation/README.md`, `evaluation/schemas/`, `evaluation/synthetic/`, `scripts/evaluate_audit_quality.py`; cập nhật docs pilot/runbook/worksheet hiện có. Reference thật và reports không commit Git.
Làm: tối thiểu 3 nhóm hồ sơ: lỗi đã xác nhận, không lỗi trong scope đã kiểm độc lập, dữ liệu thiếu/mơ hồ. Chuẩn bị Kênh Xáng từ lỗi có bằng chứng được Thang/kỹ sư xác nhận; không suy nhãn từ câu chuyện “trước đây có lỗi”. Tách development/holdout theo tài liệu gốc/revision để tránh trùng dữ liệu.
Evaluator nhận mapping adjudication do người duyệt, không tự fuzzy-match rồi tính TP. Version source hashes, labels, model/skill/prompt/config và giữ mọi attempt. Xuất counts + metrics + invalid/partial denominator notes theo §4.8.
Tests: duplicate finding không tăng TP; zero denominator N/A; novel finding chưa duyệt không thành TP; unresolved không thành clean; failed run không biến mất; revision mismatch bị loại có lý do và vẫn nằm inventory; holdout không nằm runtime mounts.
Nghiệm thu: cùng inputs/mapping cho cùng metrics; kết quả baseline giữ nguyên khi nhãn được cập nhật. Không đề xuất fine-tune trên holdout.

### Q11 — Pilot tích hợp, nghiệm thu và rollout

Dependency: Q01–Q10. Owner: lead + reviewer kỹ thuật; agent chỉ chạy checklist được giao.
Files: runbook/checklist deployment cần thiết, không sửa auth/provider ngoài scope.
Thực hiện: test suite backend theo CI trong venv; kiểm synthetic flow từ upload -> 3 pass batches -> correction -> recheck -> report -> signoff -> complete; kill/restart và race cases. Live model pilot chỉ sau quyền dùng nguồn và isolation đạt; credentials không nằm trong tool-readable paths. Lưu kết quả failed/partial và limitation.
Rollout: chế độ observe ban đầu chỉ ghi gate diagnostics, không thay quyền công bố hiện tại; số case/việc signoff phải minh bạch. Sau nghiệm thu mới bật enforce cho job mới; legacy pending kiểm lại, legacy completed giữ nguyên. Không tắt enforce âm thầm khi lỗi; pause audit mới, giữ drafts và dữ liệu, sửa/rollback artifact có kế hoạch tương thích schema.
Nghiệm thu: tiêu chí §4.8 đạt trên bộ đã chốt; mọi violation ở ma trận §6 bị phát hiện; reviewer kiểm được nguồn và lý do. Chỉ lead triển khai sau báo cáo nghiệm thu, không tự coi plan này là lệnh deploy.

## 6. Ma trận nghiệm thu bắt buộc

| Tình huống | Kỳ vọng |
|---|---|
| AI tạo đủ Excel/PDF nhưng không ledger/pass2 | Draft hiện rõ thiếu; complete 409 |
| Model ghi PASS giả vào work | Receipt backend không chấp nhận |
| Pass1 thiếu evidence một cấu kiện | Giữ unresolved/blocker đúng loại; phần độc lập tiếp tục, không global PASS giả |
| Tất cả trang có record nhưng một dòng detail bị bỏ | Coverage object/row phát hiện thiếu; page coverage không che lỗi |
| Tái kiểm cùng context | Không ghi blind/independent |
| Lỗi cũ có 12 target, chỉ kiểm 11 | Chặn hoàn tất checklist |
| Nguồn hoặc giả thiết đổi sau gate | Dependent results/receipt/signoff stale |
| Worker chết lúc ghi kết quả | Không nhận snapshot nửa chừng, attempt cũ được bảo toàn |
| False positive bị bác bỏ | Thu hồi issue/mark/counter đồng bộ |
| Memory service lỗi | Tiếp tục R-* với limitation, không giả ghi đã đọc memory |
| Model muốn approve lesson | Không có quyền |
| Dữ liệu reference bị model nhìn thấy | Evaluation không hợp lệ, không chấm thành pass |
| Một critical reference bị sót | Pilot không đạt, ghi case regression sau adjudication |
| Admin sửa report sau ký | Phải QA/validate/ký lại |
| Customer truy lịch sử/receipt của người khác | 404/403 theo policy hiện hành |

## 7. Trình tự giao việc và tiêu chí dừng mỗi đợt

Đợt A: Q00 -> Q01 -> Q02 -> Q03. Kết quả: không còn đánh đồng đủ file với đủ kiểm tra; admin thấy phần thiếu.
Đợt B: Q04 -> Q05 -> Q06 -> Q07. Kết quả: batch thực thi được quản lý, restart/correction không làm mất hoặc giữ sai trạng thái.
Đợt C: Q08 -> Q09 -> Q10. Kết quả: kinh nghiệm được duyệt trở thành checklist và có phép đo chất lượng.
Đợt D: Q11. Kết quả: pilot có bằng chứng và quyết định rollout của người phụ trách.

Có thể chuẩn bị nhãn Q10 và khảo sát kho bài học Q08 trong khi làm A, nhưng không viết chung các file runner/model. Không chạy 12 agent đồng thời. Sau mỗi task lead review diff + test + contract conformance rồi mới giao task kế tiếp. Nếu task quá lớn, tách theo hàm/API/test fixture và giữ nguyên tiêu chí nghiệm thu, không giao một lần cho agent tự thiết kế lại.

Thang/kỹ sư cần tham gia ở ba điểm: xác nhận nhãn lỗi/báo sai; duyệt bài học dùng chung; ký nghiệm thu pilot. Phần thiếu ý kiến này không cản agent xây plumbing với fixture tổng hợp, nhưng không được tự tuyên bố chất lượng kỹ thuật đạt.

### 7.1 Chia task lớn thành lượt giao nhỏ cho agent OpenCode

Không giao toàn bộ Q02/Q04/Q06/Q10 trong một prompt. Dùng các lượt sau, nghiệm thu từng lượt trước khi chuyển tiếp; agent chỉ được chạm các file tối thiểu tương ứng trong task cha. Một lượt thường là một module + test, hoặc một điểm tích hợp + test. Nếu cần quá 3 file implementation, lead tách tiếp trước khi giao.

| Lượt | Kết quả duy nhất | Điều kiện trước |
|---|---|---|
| Q00a | Mapping schema/path/validator thực tế | Khảo sát read-only |
| Q00b | Chốt contract, fixtures, baseline và prerequisite isolation | Q00a + lead duyệt |
| Q01a | Snapshot + digest + kiểm path | Q00b |
| Q01b | Wrapper validator + lỗi/timeout + receipt | Q01a |
| Q02a | Bảng quality review + idempotent migration | Q01b |
| Q02b | Runner finalization gắn receipt đúng run | Q02a |
| Q02c | Complete/status/output edit guards + race tests | Q02b |
| Q03a | Coverage calculator + fixture bỏ sót | Q02c |
| Q03b | Admin API authorization và schema response | Q03a |
| Q03c | Admin UI hiển thị response đã khóa | Q03b |
| Q04a | Work-item state machine và prerequisite scheduler thuần dữ liệu | Q03a |
| Q04b | Ledger patch scope validation + atomic merge | Q04a |
| Q04c | Batch subprocess orchestration + budget/repair limits | Q04b |
| Q04d | Tích hợp worker/recovery với controller | Q04c |
| Q05a | Pass2 payload/context isolation và provenance | Q04d |
| Q05b | Trusted sanity checker và các phép số học đã chốt | Q05a |
| Q05c | Pass3 issue/case target closure | Q05b |
| Q06a | Dependency traversal/cycle/missing-node behavior | Q05c |
| Q06b | Correction invalidation và thu hồi signoff | Q06a |
| Q06c | Reuse snapshots vào attempt mới và kiểm crash | Q06b |
| Q07a | Export contract từ ledger và counts thống nhất | Q06c |
| Q07b | Annotation/source anchor QA + report replacement guards | Q07a |
| Q08a | Lesson version store và lifecycle | Q00b; branch riêng |
| Q08b | Admin review/approval và scope permissions | Q08a |
| Q09a | Lesson selector/dispositions theo rule | Q08b + Q06c |
| Q09b | Gắn MEM-* vào regression/pass3, giữ version theo run | Q09a |
| Q10a | Case/reference schema + fixtures tổng hợp | Q00b |
| Q10b | Scorer deterministic từ adjudicated mapping | Q10a |
| Q10c | Nhãn thật, split development/holdout, isolation + run records | Q10b + reviewer |
| Q11a | Synthetic end-to-end và failure matrix | Q07b + Q09b + Q10b |
| Q11b | Pilot thật, adjudication và quyết định rollout | Q11a + quyền dữ liệu/isolation |

Q00b phải ghi rõ cách enforce controller-owned receipts và cách giấu đáp án pass1/holdout khỏi tool runtime. Nếu chưa có cơ chế được kiểm chứng, vẫn làm unit/synthetic tasks, nhưng Q02 enforce, Q05 blind và Q11 live chưa được nghiệm thu. Lead quyết định thay đổi runtime riêng; không giao agent tự mở rộng quyền để vượt blocker.

## 8. Mẫu prompt và handoff

Dùng [OPENCODE_TASK_TEMPLATE.md](OPENCODE_TASK_TEMPLATE.md). Người điều phối điền task ID, baseline SHA, allowed files, dependency commits, lệnh test thật và đường dẫn fixtures trước khi gửi. Không bảo agent tự tìm credentials hoặc tự chọn dữ liệu thật.

Checklist lead trước merge: scope đúng; không bỏ validation; không có secret/data; contract tương thích; tests liên quan pass; evidence không giả; docs đúng trạng thái; không sửa job lifecycle ngoài task; phiên bản bundle skill tương ứng được đóng gói và kiểm digest.

## 9. Trạng thái thực hiện

- [ ] Q00 baseline/contract
- [ ] Q01 validator adapter
- [ ] Q02 publication gate
- [ ] Q03 coverage UI/API
- [ ] Q04 batch controller
- [ ] Q05 pass evidence
- [ ] Q06 invalidation/resume
- [ ] Q07 report/evidence QA
- [ ] Q08 lesson review
- [ ] Q09 lesson applicability
- [ ] Q10 evaluation corpus/scorer
- [ ] Q11 integrated pilot/rollout

Chỉ đánh dấu khi có commit, lệnh kiểm tra và kết quả nghiệm thu. Một task agent nói “done” chưa đủ để đánh dấu.
