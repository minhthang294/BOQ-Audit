# Q05g — Sửa stage gate trên cùng AuditRun

Ngày: 27/10/2026. Trạng thái 28/10: G1/G2/G3 và một phần G4 có code local; chưa bật pilot.

## Vì sao cần hợp đồng mới

Hiện tại `execute_next_checkpoint_batch()` chỉ chạy work item `PENDING`. Sau khi mọi batch của pass thành `DONE`, validator có thể trả `BLOCKED`; worker đưa job về `WAITING_FOR_INFO`. Không có batch nào còn `PENDING` để sửa. `AuditRunBatchResult` có ràng buộc duy nhất `(run_id, batch_key)` và `load_run_checkpoint()` đối chiếu ledger với kết quả bất biến, nên đổi trực tiếp ledger hoặc chạy lại cùng batch đều không hợp lệ. Không xóa hay ghi đè kết quả cũ để mở gate.

`memory_lookup=UNAVAILABLE` có lý do là disposition hợp lệ theo validator v6.4. Nó phải hiện là giới hạn kỹ thuật, không tự xem là bằng chứng đã tra memory. Hai cờ Pass 3 `false_positive_propagation_complete` và `target_fit_check_complete` trước đây ghi cứng `false`; checker local hiện đặt `true` khi toàn bộ trang/target có review Pass 3 ràng buộc phạm vi, mọi candidate được gắn đúng một issue cuối qua `candidate_ids` và issue còn mở có review phủ nguồn. Với false positive/issue đã đóng, checker lấy candidate work item làm gốc, duyệt dependency graph trong inventory được duyệt, rồi yêu cầu mỗi descendant Pass 3 có review gắn issue ID và phủ đủ page/sheet/target scope. Không xác định được gốc hoặc thiếu review thì gate chặn. Target chỉ có sheet nay dùng review với `sheet_ids` khớp source refs và inventory đã duyệt. Quan hệ domain record → report/annotation vẫn chưa được mô hình hóa nên dependency graph không chứng minh được mọi downstream output. Các lỗi controller hoặc skill contract được phân loại riêng, không tiêu hai lượt sửa của model.

## Bất biến

1. Chỉ sửa pass hiện tại của latest `AuditRun`, khi job đang chờ, checkpoint `READY`, không có batch đang chạy, receipt gate mới nhất là `BLOCKED`, nguồn/skill/inventory approval vẫn khớp digest. Không sửa pass đã qua gate. Nguồn hoặc approval thay đổi thì tạo run mới.
2. Reviewer chọn một batch trong batch plan cùng pass và các `blocking_checks` cụ thể cần sửa. Controller giữ nguyên `check_ids`, source grants và timeout. Không cho model đổi inventory, phạm vi, kết quả pass khác hoặc cờ gate.
3. Bản sửa là **full replacement** của patch, evidence và domain records thuộc batch đó. Lưu append-only revision; kết quả gốc và mọi revision trước vẫn đọc được. Chỉ revision đã validate và commit nguyên tử mới trở thành effective result. Nếu validation thất bại, effective result cũ giữ nguyên và gate vẫn chặn.
4. Mỗi batch có tối đa 1 lần đầu + 2 lần repair trong cùng run, bất kể fingerprint thay đổi. Fingerprint = SHA-256 canonical JSON của `pass_no`, `batch_key` và danh sách blocker đã chuẩn hóa; không dùng snapshot ID hay câu trả lời model làm nguồn quyết định budget. Hết budget thì dừng và hiện `BLOCKED` cho kỹ sư.
5. Khi revision thay đổi kết quả, tính lại ledger/projection từ **effective results** của toàn run, đối chiếu ID/evidence/cross references và tăng checkpoint version bằng CAS trong cùng transaction. Receipt gate cũ vẫn giữ; chỉ gate mới có thể chuyển pass. Không publish báo cáo trước khi kỹ sư duyệt.
6. Nếu blocker liên quan work item khác hoặc output downstream đã dùng kết quả cũ, không tự sửa cục bộ: ghi pending rechecks và thực hiện Q06 invalidation trước. Nếu không chứng minh được dependency, dừng với blocker rõ ràng.

## Thay đổi tối thiểu theo thứ tự

### G1 — Phân loại blocker và chặn retry vô ích

- Đọc receipt `audit_run_stage_gates` cuối cùng trên current pass, xác thực SHA/scope như API quality hiện có. Trả `MODEL_REPAIRABLE`, `CONTROLLER_CONTRACT`, `SOURCE_OR_APPROVAL_CHANGED`, `DEPENDENCY_RECHECK_REQUIRED`, hoặc `UNKNOWN`; `UNKNOWN` không tự chạy model.
- Cờ controller ghi cứng, thiếu protocol field, lỗi adapter và source drift là `CONTROLLER_CONTRACT`/`SOURCE_OR_APPROVAL_CHANGED`. Chỉ blocker có mapping rõ đến record ID/check ID trong batch hiện tại mới là `MODEL_REPAIRABLE`.
- API quality hiển thị loại blocker và hành động tương ứng; không hiện nút repair cho loại chưa thể sửa. Không cho retry gate lặp lại cùng version/projection mà không có revision mới.

### G2 — Revision bất biến cho một batch

- Thêm bảng `audit_run_batch_revisions`: `run_id`, `base_result_id`, `revision_no`, `gate_id`, `blocker_fingerprint`, `patch_json`, `evidence_json`, `records_json`, các SHA, `checkpoint_version`, `created_at`; unique `(base_result_id, revision_no)`. Giữ bảng `audit_run_batch_results` hiện tại không đổi để migration cộng thêm an toàn.
- `load_run_checkpoint()` validate chuỗi revision, digests, cùng pass/batch/source grants, rồi chọn revision mới nhất cho từng base result. Rebuild ledger từ effective patches; validate domain records của effective set. Dữ liệu lịch sử vẫn được kiểm riêng, nhưng duplicate ID qua các revision lịch sử của **cùng batch** không phải trùng trong effective set.
- API/worker chỉ nhận revision qua cùng `validate_batch_payload()` và `validate_batch_ledger_records()` với đúng scope đã duyệt. Chặn revision nếu ID/evidence va chạm với effective results của batch khác. Transaction chứa revision + checkpoint CAS; lỗi ở một bước rollback cả hai.

### G3 — Worker repair có budget

- Thêm request repair có trạng thái `REQUESTED/RUNNING/FAILED/COMMITTED`, gắn `run_id`, `batch_key`, `gate_id`, fingerprint và số lần. Chỉ reviewer allowlisted tạo request; không nhận patch thô từ browser. Worker ưu tiên request trên job đã chờ, tạo scoped context từ nguồn gốc, blocker và batch cũ, gọi Codex trong sandbox hiện tại, rồi commit G2.
- Restart khi `RUNNING`: đánh request `FAILED` và giữ nguyên effective result; reviewer có thể thử lại nếu còn budget. Không tạo AuditRun mới. Sau commit, worker chạy lại stage gate một lần trên checkpoint version mới; PASS mới được chuyển pass.
- Không tự động lặp khi cùng fingerprint còn blocker; admin nhìn thấy lần đã dùng và chọn batch/nguồn cần sửa. Tối đa hai lần repair kể cả khi blocker đổi chữ.

### G4 — Bằng chứng Pass 3 do controller kiểm

- Checker hiện dùng `candidate_ids` để tìm work item gốc, sau đó đối chiếu graph đã duyệt và yêu cầu các descendant Pass 3 có review gắn `issue_id`, phủ đủ scope và mang evidence đã qua batch validation. `false_positive_propagation_complete` vẫn false nếu không tìm được gốc hoặc thiếu bất kỳ review nào. Graph hiện chỉ bao phủ work item, chưa đủ để kết luận mọi liên kết record/output downstream.
- `target_fit_check_complete` chỉ true khi từng approved target có review Pass 3 đúng target ID, source anchor và evidence; target thiếu hoặc không áp dụng phải có lý do có nguồn. Dùng checker xác định trên effective ledgers, không lấy boolean model tự khai.
- Nếu hợp đồng chưa đủ biểu đạt các điều trên, giữ gate `BLOCKED` và báo chính xác `CONTROLLER_CONTRACT`. Cập nhật skill/pin và fixture đồng thời với thay đổi protocol; không âm thầm đặt cờ true.

## Kiểm nhận cho agent triển khai

- Fixture một batch DONE, gate BLOCKED, revision hợp lệ: base result không đổi, revision mới là effective, checkpoint version tăng, gate mới PASS thì chuyển pass đúng một lần.
- Revision sai scope, thiếu evidence, trùng record ID với batch khác, SHA sai, stale version/source/approval hoặc writer race: không đổi effective result, ledger hay pass.
- Lần repair thứ ba bị từ chối; hai lỗi adapter không làm mất base evidence. Restart giữa model và commit không tạo revision nửa chừng.
- Blocker Pass 3 do issue/candidate thiếu liên kết downstream không kích hoạt model repair; UI ghi `CONTROLLER_CONTRACT`, run vẫn chờ.
- Sau correction thay số lượng/issue, downstream phụ thuộc bị invalidated hoặc repair bị chặn chờ Q06; không giữ kết luận cũ như còn hiệu lực.

G1 và G4 phải đi trước bật repair tự động. G2/G3 chỉ merge khi có kiểm tra migration dữ liệu cũ, concurrency, restart và canary Codex thật. Cờ `AUDIT_CHECKPOINT_WORKER_ENABLED` tiếp tục mặc định `false`.

Tiến độ 29/10: API quality và UI hiện `gate_repair_status` bảo thủ; reviewer allowlisted có thể yêu cầu sửa batch khi gate BLOCKED, tối đa hai yêu cầu cho từng batch kể cả lượt thất bại. Revision lưu manifest invalidation + digest; checker xác nhận chính xác closure theo dependency graph và các result batch đã tồn tại. Work item downstream chuyển DONE→INVALIDATED→PENDING, ledger/result cũ bị loại khỏi effective projection; worker rerun batch và ghi revision append-only. Migration bổ sung cột invalidation cho DB cũ. Nếu graph sai thì fail closed. G2 kiểm revision, gate/source/skill/approval/CAS và chỉ dùng bản hiện hành. Checker Pass 3 xử lý issue còn mở có review phủ nguồn; candidate phải gắn đúng một issue cuối có phạm vi phủ nguồn. False positive/issue đã đóng chỉ được qua khi các descendant Pass 3 theo graph có review gắn issue và phủ đủ source scope. Target chỉ có sheet có thể qua khi review khai rõ `sheet_ids` đúng phạm vi; page coverage vẫn phải đầy đủ. Dependency vẫn chỉ thể hiện các cạnh đã có trong approved work-item graph, chưa chứng minh quan hệ giữa mọi domain record/output; cần kiểm inventory graph có đủ cạnh trước canary. Chưa chạy live Codex canary hoặc triển khai production; cờ pilot mặc định false.

Bổ sung 29/10: Closure của false positive/issue đã đóng còn phải có ít nhất một `pass3_review.issue_ids` gắn đúng issue và phủ toàn bộ source refs của chính issue, kể cả khi dependency graph không có descendant Pass 3. Checker chấp nhận nhiều candidate từ cùng một work item; candidate ID thiếu hoặc không resolve được work item gốc vẫn chặn. Stage gate là metadata kỹ thuật, không tự phát hành.

Cổng ký duyệt/công bố nay đọc receipt Pass 3 mới nhất, kiểm digest bản ghi, run, nguồn, skill, checkpoint version, PASS/exit 0, stage-complete status và danh sách blocker rỗng. Một receipt BLOCKED mới hơn khiến signoff hiện hành không thể dùng để công bố, dù Excel/PDF draft còn đủ digest/QA. Report source digest chứa toàn bộ effective ledger, version, gate reports Pass 1/2 và source/skill/inventory digests; PDF/Excel draft cùng reviewer events gắn snapshot/classification digest. Điều này làm stale toàn bộ output khi checkpoint/classification đổi. Graph hiện chưa biểu đạt riêng quan hệ từng domain record đến report/annotation, nên vẫn cần corpus và kỹ sư kiểm tính đầy đủ các cạnh trước canary.

Bổ sung 29/10: Hợp đồng `pass2_review`/`pass3_review` và validator v6.4 cho phép review có `page_ids` hoặc `sheet_ids` không rỗng. Gate Pass 3 đối chiếu sheet ID trong payload, source refs và approved inventory, kể cả với issue đã đóng; không tự tính có review chỉ vì source refs chứa sheet. Bundle pin và trusted digest được cập nhật; cần đồng bộ image/skill VM trước pilot.

Bundle phân phối local được đóng gói tại `skill-bundle/boq-audit-v6.4.zip` và kiểm manifest/hash bằng `scripts/prepare_pinned_skill.py`; CI giải nén trước backend tests. Deploy vẫn fail-closed cho tới khi host mount skill trên VM khớp digest mới.
