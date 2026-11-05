# Điều phối 2–3 agent OpenCode qua Herd

Ngày: 2026-10-23. Trạng thái: đang thực thi; Q00a khảo sát và Q00b contract đã nghiệm thu, Q01a/Q08a/Q10a-schema đang chạy trong worktree riêng.
Kế hoạch gốc: [BOQ_AUDIT_QUALITY_PLAN.md](BOQ_AUDIT_QUALITY_PLAN.md).
Task card: [OPENCODE_TASK_TEMPLATE.md](OPENCODE_TASK_TEMPLATE.md).

## 1. Môi trường đã kiểm tra

- BOQ local main: `8cdacddc3ec8187d29d20d2bc1792197abe55bdd`; không có tracked changes trước khi viết kế hoạch này. Có `audit_runs/`, `exports/` untracked, không đưa vào checkout agent.
- Herd 0.9.3 đang chạy, protocol tương thích; phiên điều phối có HERDR_ENV=1.
- OpenCode v2.1.2 đã cài; Herd hỗ trợ `agent start --kind opencode`.
- Lúc kiểm tra, workspace w1 có một pane Codex w1:p18, cwd là CAD-Agent-Demo. ID chỉ là bằng chứng thời điểm kiểm tra; phải đọc lại trước thao tác.
- Chưa kiểm tra provider/model/quota của OpenCode bằng một lượt trả lời. Không coi có CLI là model đã dùng được.

## 2. Bố trí và trách nhiệm

Giữ pane điều phối hiện tại. Khi bắt đầu thực thi, thêm 3 pane OpenCode trong cùng tab, ưu tiên bố trí 2×2 theo kích thước thực tế, giữ focus của người dùng. Mỗi pane dùng checkout BOQ riêng, không kế thừa cwd CAD.

| Pane / tên agent | Phạm vi chính | Không tự làm |
|---|---|---|
| Codex điều phối | Chốt contract, phân task, nghiệm thu, giải quyết xung đột, tích hợp, giao lượt kế tiếp | Không chấp nhận DONE chỉ từ lời agent |
| `boq-core` | Q01–Q07: validator, gate, coverage, batch, pass, recovery, báo cáo | Không đổi contract, dung sai hay lifecycle ngoài card |
| `boq-lessons` | Q08: kho bài học; Q09 sau khi Q06c đạt | Không tự VERIFIED bài học, không sửa runner cùng core |
| `boq-eval` | Q10: schema/fixture/scorer; phản biện và kiểm thử độc lập | Không tự đặt nhãn kỹ thuật hoặc nhìn đáp án trong audit runtime |

Đây là vai trò mặc định; lead có thể giao task nhỏ khác khi dependency chưa sẵn sàng. Tối đa 3 agent làm việc; không cần giữ cả 3 bận bằng việc chưa đủ điều kiện.

## 3. Các đợt chạy song song

### Đợt 0 — Khảo sát Q00a, chưa thay runtime

Ba agent đọc cùng baseline, báo cáo riêng:

- Core: mapping runner/worker/recovery, admin complete/status/output; xác định mọi đường công bố và transaction guard.
- Lessons: mapping skill bundle thực tế, ledgers, schema, validator, R-*, regression/annotation/export helpers; ghi digest và mâu thuẫn tài liệu/code.
- Eval: mapping tests/CI/pilot, fixture tổng hợp, isolation của receipt/pass2/holdout; ghi lệnh test có thật và prerequisite thiếu.

Lead tổng hợp Q00b thành một data contract duy nhất; chỉ lead sửa contract chung. Mọi field phải truy về schema/code hoặc đánh NEW. Chốt receipt ownership, digest, status, error, counts, lesson record, evaluation mapping và đường dẫn artifact trước implementation. Nếu hợp đồng kho bài học/evaluation chưa đủ, hai lane đó tiếp tục khảo sát, chưa viết module.

### Đợt 1 — Sau Q00b

| Core, theo thứ tự | Lessons, theo thứ tự | Eval, theo thứ tự |
|---|---|---|
| Q01a → Q01b → Q02a → Q02b → Q02c → Q03a → Q03b → Q03c | Q08a → Q08b | Q10a → Q10b |

Q08a có thể viết store thuần dữ liệu theo contract đã khóa. Q08b phải chờ lead xếp lượt chỉnh models/schema/admin nếu core đang làm Q02/Q03; tách API riêng chỉ khi kiến trúc hiện có phù hợp, không tạo module chỉ để né xung đột. Eval dùng synthetic fixtures, không đợi pipeline hoàn chỉnh để viết scorer.

### Đợt 2 — Controller và tính nhất quán

- Core: Q04a → Q04b → Q04c → Q04d → Q05a → Q05b → Q05c → Q06a → Q06b → Q06c.
- Lessons: hoàn tất phần Q08 còn lại; review các tình huống applicability, version, revoke bằng fixtures riêng. Chờ Q06c trước Q09.
- Eval: kiểm thử bổ sung đúng task đã nghiệm thu, đối chiếu ma trận failure; chuẩn bị Q10c offline nếu có nguồn/nhãn được duyệt. Chưa có reviewer thì ghi dependency, không tự tạo nhãn thật.

Q04a chỉ bắt đầu sau toàn bộ Q03 nghiệm thu theo dependency task cha; không dựa vào Q03a riêng để bỏ qua cổng đó. Contract batch phải được lead duyệt trước Q04.

### Đợt 3 — Sau Q06c

- Core: Q07a → Q07b, xuất báo cáo từ ledger và QA annotation.
- Lessons: Q09a → Q09b, gắn bài học đã duyệt vào checklist và pass3.
- Eval: Q10c trong phạm vi được cấp; chuẩn bị checklist Q11a.

Q07 và Q09 chỉ song song khi không cùng sửa workflow/quality/API. Lead khóa quyền ghi theo file và xếp lượt phần tích hợp giao nhau. Không cho hai agent cùng sửa skill bundle đang cài; mỗi thay đổi skill nằm trong bản sao được pin, lead đóng gói cuối.

### Đợt 4 — Nghiệm thu và rollout

Sau Q07b + Q09b + Q10b: lead giao Q11a trên checkout tích hợp, kiểm đủ failure matrix. Q11b cần quyền dữ liệu, isolation đã xác minh và reviewer kỹ thuật; không lấy synthetic pass làm chứng nhận chất lượng hồ sơ thật. Bắt đầu observe, chỉ enforce sau nghiệm thu. Kế hoạch này không phải lệnh deploy.

## 4. Checkout và quyền ghi

- Khi thực thi, lead chọn baseline mới đã xác minh, tạo branch tích hợp và checkout riêng cho từng lane. Không sửa/reset/stash workspace người dùng; giữ nguyên audit_runs/exports.
- Ưu tiên Git worktree riêng dưới vùng làm việc được cấp, mỗi task một branch; tạo dưới cây checkout không tự tạo ranh giới bảo mật.
- Agent chỉ được sửa danh sách file trong card; lead quản lý quyền ghi độc quyền cho runner, worker, entities, schema, admin API và skill bundle.
- Branch task dựa trên integration SHA có đủ dependency đã nghiệm thu; cập nhật khi agent idle và checkout sạch. Không cherry-pick giữa các task chưa duyệt.
- Agent không push/merge/deploy, không truy cập credential, production, hồ sơ khách hàng. Chỉ fixture tổng hợp và bundle đã cấp.
- Worktree và prompt chỉ giúp tổ chức: trước khi giao dữ liệu thật phải kiểm chứng sandbox/filesystem isolation. Q02 enforce/Q05 blind/Q11 live không đạt nếu chưa có isolation tương ứng.

## 5. Vòng điều phối trong Herd

1. Đọc lại `HERDR_ENV`, pane/layout/agent IDs. Dùng CLI hiện có, không đoán ID hoặc pane đang focus.
2. Chốt checkout BOQ tuyệt đối; tạo pane bằng `pane split ... --cwd <checkout> --no-focus`, chọn right/down theo layout. Agent start không tự tạo pane.
3. Khởi động `herdr agent start <name> --kind opencode --pane <returned-id>`. Chờ ready; kiểm model/provider hiện có, chạy prompt nhỏ không có dữ liệu riêng trước task đầu tiên. Không tự đổi provider vì quota.
4. Giao đúng một card qua `agent prompt`, gồm baseline, dependency, allowed files, contract, acceptance và exact test command. Không giao toàn bộ kế hoạch để agent tự chọn việc.
5. Theo dõi `agent get/read`; wait tối đa 30–60 giây mỗi lần để vẫn cập nhật người dùng. Status done/idle chỉ kích hoạt review, không tự đánh task DONE.
6. Nếu timeout/stalled: đọc trạng thái/output trước khi gửi lại, tránh task trùng. Nếu blocked bởi câu hỏi/approval: đọc nội dung, không tự gửi Enter hoặc chấp thuận chung. Lead xử lý quyết định trong phạm vi đã được giao; yêu cầu quyền thực sự thiếu phải đưa cho người dùng.
7. Kiểm handoff theo template, diff, scope, contract, tests và dấu hiệu nới gate. Lead chạy lại kiểm tra cần thiết trên bản tích hợp; chỉ sau đó commit/merge nội bộ và mở dependency tiếp theo.
8. Agent sửa tối đa hai lượt cho cùng blocker không đổi; nếu vẫn lỗi, lead nhận xử lý hoặc tách task. Phần độc lập tiếp tục; không nới tiêu chí để cho qua.

Không dùng agent nội bộ thay OpenCode pane; ba agent thực thi đều là OpenCode người dùng nhìn thấy. Không đóng pane có sẵn hoặc restart Herd.

## 6. Bằng chứng và phục hồi phiên

Khi bắt đầu thực thi, lead tạo tracker với task, owner, pane ID, branch, baseline/dependency SHA, allowed files, trạng thái, evidence/handoff và next action. Chỉ lead sửa tracker.

Trạng thái: READY → RUNNING → REVIEW → ACCEPTED; BLOCKED/REWORK khi cần. ACCEPTED cần commit và bằng chứng nghiệm thu. Mỗi task bàn giao lệnh test, exit code, số pass/fail/skipped, deviation và giới hạn thật. Lưu log ngoài Git; không dựa riêng vào scrollback Herd.

Sau gián đoạn: đọc tracker + Git + trạng thái agent; đối chiếu output đã có trước khi gửi prompt mới. Không nhận câu “đã làm” nếu không có diff/receipt. Giữ failed attempts để phân tích.

## 7. Phương án 2 agent và mốc báo cáo

Nếu tài nguyên/quota không đủ: giữ core; gộp lessons/eval vào agent thứ hai nhưng chạy từng task tuần tự trong checkout riêng. Lead vẫn review; không tự giảm tiêu chí. Không hứa nhanh gấp ba vì Q01–Q07 có chuỗi phụ thuộc dài.

Báo người dùng sau Q00b, Q03, Q06, Q07+Q09+Q10 và Q11: đã nghiệm thu gì, blocker, task đang chạy, bước kế tiếp. Mỗi đợt chỉ báo chất lượng kỹ thuật khi có bằng chứng đánh giá tương ứng.

Bước đang chạy: triển khai ba task Q01a, Q08a, Q10a-schema bằng card hẹp sau Q00b. Backend tests sẽ chạy tuần tự sau khi các agent bàn giao; Q02 enforce/Q05 blind/Q11 live vẫn chờ isolation được chứng minh.
