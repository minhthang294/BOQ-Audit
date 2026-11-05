# BOQ quality — trạng thái điều phối

Khởi tạo: 2026-10-23. Lead: Codex. Kế hoạch: OPENCODE_PARALLEL_EXECUTION_PLAN.md.

Baseline: `8cdacddc3ec8187d29d20d2bc1792197abe55bdd`.
Integration branch: `boq-quality/integration`.
Checkout root: `/home/thangpham/Projects/BOQ Audit/.worktrees/quality-20261023/`.
Skill: 62 file, digest `17c14f63f78c2670428e233c4bfc56623473d1d644cf38d003db477d80c14a2c`; manifest pin `docs/plans/BOQ_AUDIT_SKILL_MANIFEST.json` đã được copy vào ba checkout, hash khớp `8469271bc1cdbdc81aea5ea31481c35448426f491f44d024419782cb69dd3a40`.

| Task | Owner | Pane | Branch | Trạng thái | Phạm vi |
|---|---|---|---|---|---|
| Q00a-core | boq-core | w1:p1H | boq-quality/q00-core | REVIEWED | Lifecycle/publish paths; đề xuất log event bất biến cho signoff/revoke |
| Q00a-lessons | boq-lessons | w1:p1J | boq-quality/q00-lessons | REVIEWED | Validator thật, bundle/schema, R-* và enum |
| Q00a-eval | boq-eval | w1:p1K | boq-quality/q00-eval | REVIEWED | Fixtures tổng hợp và failure matrix; backend suite chưa chạy |
| Q00b | Lead + core + lessons review | w1:p18 | boq-quality/integration | ACCEPTED | Data contract `integration/docs/plans/BOQ_AUDIT_DATA_CONTRACT.md`; manifest và synthetic fixtures đã đối chiếu |
| Q01a | boq-core | w1:p1H | boq-quality/q00-core | ACCEPTED · `9645b6f` | Snapshot/digest/path safety; 44 unit cases pass; validator/receipt chưa làm |
| Q01b | boq-core | w1:p1H | boq-quality/q00-core | ACCEPTED · `2152aab`, `ff3b458` | Trusted validator wrapper + fixed trusted skill digest + strict report parsing + atomic receipt; generated `__pycache__` ignored only for bundle walk; 87 unit cases pass |
| Q02a | boq-core | w1:p1H | boq-quality/q02a-schema | ACCEPTED · `8728de5` | Review/event schema + reviewer assignment + idempotent startup migration; migration test pass |
| Q02b | boq-core + lead fixes; boq-eval review | w1:p1H / w1:p1K | boq-quality/q02b-runner | ACCEPTED · `82cf891`, `92945fa`, `5399922` (merges `4e8c444`, `b58d504`, `ffb0521`) | Release-stage advisory review; correct controller root; failed/missing-output/source-drift cannot retain PASS; unverified model counters null; stale run snapshot/receipt skipped or cleaned |
| Q02c | boq-core; boq-eval review | w1:p1H / w1:p1K | boq-quality/q02c-invalidation | ACCEPTED · `bd83102` (merge `26305ee`) | Observe-only mutation invalidation for admin/customer fields, outputs and retry; reviewed job hard-delete returns 409; complete/download/status remain ungated |
| Q03a | Lead; boq-eval review | w1:p18 / w1:p1K | boq-quality/integration | ACCEPTED · `0b4f25a` | Coverage calculator compares trusted expected work items with execution ledger; missing scope, stale revisions, unresolved, blocked, N/A, evidence and zero denominator tested |
| Q03b | Lead; OpenCode review | w1:p18 / w1:p1K / w1:p1M | boq-quality/integration | REVIEWED · `d075b8b`; no API findings | Admin-only quality summary; 401/403/404; latest review, invalidation freshness, allowlisted summary, no-store; coverage honestly unavailable pending trusted runtime inventory |
| Q03c | Lead; OpenCode review | w1:p18 / w1:p1K / w1:p1M | boq-quality/integration | REVIEWED · `3b5a840`; Medium freshness-label finding fixed and re-review ACCEPTED | Admin detail quality panel shows advisory status, freshness, blockers, null/unavailable counts and permitted attempt artifacts; typecheck pass |
| Q04a | OpenCode; lead review | w1:p1M | boq-quality/integration | IMPLEMENTED · focused tests 11/11 pass; OpenCode review ACCEPTED; uncommitted | Pure-data work-item transitions and deterministic direct-prerequisite scheduler; no persistence, worker, global gate, or stage advancement |
| Q04b | OpenCode; lead review | w1:p1M | boq-quality/integration | IMPLEMENTED · focused tests 22/22 pass; OpenCode review ACCEPTED; uncommitted | All-or-nothing in-memory ledger patch merge, exact batch membership/RUNNING state, strict allowed fields and result/evidence validation |
| Q04c | OpenCode; lead review | w1:p1M | boq-quality/integration | IMPLEMENTED · focused tests 35/35 pass; OpenCode review ACCEPTED; uncommitted | Bounded sequential orchestration via injected adapter; max 2 repairs per batch, total call budget, fail-closed BLOCKED state, dependency skips; live subprocess/worker hookup deferred to Q04d |
| Q08a | boq-lessons | w1:p1J | boq-quality/q00-lessons | ACCEPTED · `dd3dfb7` | Versioned JSON store; 23 unit cases pass; API/DB chưa làm |
| Q10a-schema | boq-eval | w1:p1K | boq-quality/q00-eval | ACCEPTED · `9832c03` | 3 schema + 7 synthetic fixtures; JSON Schema 3/3, fixtures 7/7 |

Q00a khảo sát và Q00b contract đã nghiệm thu; core và lessons chấp nhận độc lập qua review cuối. Contract digest: `dde51fff769c2aabe94cf0ff048d775af28eae7f359850cb09df64e3fc42a460`.
Q00 fixtures synthetic được kiểm độc lập: pass1/2/3 `METADATA_STAGE_COMPLETE`, release `READY_FOR_ENGINEER_REVIEW`; bốn ca lỗi đều `BLOCKED`. Đây chỉ là kiểm metadata, không chứng nhận BOQ.
Q01b đã merge nội bộ vào integration tại `3c0948f`; Q02a merge nội bộ tại `9ed1a81`; Q02b merge tại `4e8c444`, `b58d504`, `ffb0521`. Review phát hiện và lead đã vá lệch controller root, PASS sai sau process fail/missing outputs, counter từ summary model, thiếu recheck source digest và snapshot stale mồ côi. OpenCode eval chấp nhận follow-up review Q02b. Q02c `bd83102` được eval chấp nhận và merge vào integration `26305ee`. Combined direct suite trên integration pass 132/132 với pinned bundle thật; py_compile/diff-check pass. Q02c chỉ ghi observe-only invalidation và giữ lịch sử; không bật gate xuất bản. Chưa push/deploy. Full backend suite chưa chạy; CI Python 3.12 và production isolation chưa xác minh.

## Quyết định vận hành

- Bố trí cùng tab w1:t1, 2×2; giữ focus pane người dùng.
- Fish prompt bị Herd nhận busy; chỉ ba shell mới tạo được chuyển sang bash tối giản. OpenCode đã được Herd nhận diện và báo interactive_ready cho cả ba.
- Branch/worktree riêng tránh đụng file, chưa phải sandbox bảo mật. Không cấp dữ liệu khách hàng cho khảo sát.
- Khi resume, đọc lại agent list/pane IDs và output trước khi gửi lại task.

## Bước tiếp theo

Q00b được core/lessons chấp nhận sau khi khóa event ordering, signoff binding, authorization/transitions, snapshot uniqueness, blob retention và conditional revoke. Q01a, Q08a, Q10a-schema đã được lead kiểm tra, test và merge nội bộ vào integration `ad7608e`. Q01b được independent reviewer chấp nhận sau khi neo digest cố định, thêm test bundle tamper/release partial/report fields và xử lý cache Python phát sinh. Q02a được independent reviewer chấp nhận: `8728de5`, merge ở `9ed1a81`; combined direct tests trên integration 88/88. Q02b đã lead nghiệm thu sau khi xử lý các finding review: `82cf891`, `92945fa`, `5399922`, merge `4e8c444`, `b58d504`, `ffb0521`; direct tests cùng pinned bundle pass 102/102. Q02c observe-only được eval chấp nhận: `bd83102`, merge `26305ee`; invalidation event cho mutation và 409 khi hard-delete job có review. Combined suite pass 132/132. Q03a commit `0b4f25a`, 7 test pass, eval accepted. Q03b commit `d075b8b`; 32 test liên quan pass, py_compile/diff-check pass; OpenCode review không tìm API finding. Q03c commit `3b5a840`; panel typecheck pass, chưa có visual/browser QA. OpenCode review phát hiện Medium ở nhãn `NOT_VERIFIED` “Chưa xác minh còn mới”; lead sửa thành “Chưa xác nhận còn hiệu lực” tại `frontend/components/QualityReviewPanel.tsx:5`, OpenCode xác minh và chấp nhận lại. Q04a contract revision 1 được lead duyệt; pure-data state machine/scheduler và 11 focused tests được OpenCode triển khai, lead rà soát, OpenCode review cuối ACCEPTED. Q04b contract revision 1 được lead duyệt; all-or-nothing ledger patch merge có strict scope/result validation, 22 focused tests pass và OpenCode review cuối ACCEPTED. Q04c contract revision 1 được lead duyệt; bounded sequential orchestrator qua injected adapter, tối đa 2 repair/batch, total call budget, fail-closed và bỏ qua dependent batches khi prerequisite BLOCKED; 35 focused tests pass, lead review và OpenCode review cuối ACCEPTED. Adapter subprocess thật/worker hookup và recovery thuộc Q04d, chưa live. Q03c fix và code/contract Q04a–Q04c hiện uncommitted trong integration. Coverage vẫn unavailable và unresolved chưa có dữ liệu. Bước tiếp theo là Q04d tích hợp runner/worker/recovery. Chưa push/deploy; process isolation và full backend/CI suite chưa xác minh.
