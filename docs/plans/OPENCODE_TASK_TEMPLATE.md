# Mẫu giao một task BOQ Audit cho agent OpenCode

Người điều phối phải điền các ô `<...>` trước khi giao. Không giao cả kế hoạch để agent tự chọn việc.

```text
Bạn thực hiện task <Qxx — tên> trong dự án BOQ Audit.
Mục tiêu duy nhất: <một kết quả quan sát được>.
Checkout được cấp: <absolute path>; branch: <branch>; baseline SHA: <sha>.
Dependency commits đã nghiệm thu: <sha và task>.

ĐỌC THEO THỨ TỰ:
1. AGENTS.md áp dụng trong checkout (nếu có).
2. docs/plans/BOQ_AUDIT_DATA_CONTRACT.md (chỉ sau Q00).
3. Task <Qxx> trong docs/plans/BOQ_AUDIT_QUALITY_PLAN.md và các mục hợp đồng được task viện dẫn.
4. Các file implementation/callers/tests: <danh sách cụ thể>.
5. Skill bundle được cấp: <path + expected digest>, chỉ reference cần thiết cho task.

ĐƯỢC SỬA: <file list>.
ĐƯỢC TẠO: <file list>.
KHÔNG ĐƯỢC SỬA: các file ngoài danh sách, auth, secrets, production config,
model/provider settings và các trạng thái nghiệp vụ ngoài hợp đồng.
Không deploy, push, merge, reset/clean hoặc sửa dữ liệu thật.
Không đọc hoặc gửi hồ sơ khách hàng sang AI/provider ngoài input được cấp.

HỢP ĐỒNG BẮT BUỘC:
<input schema, output schema, status semantics, failure behavior, ownership>.
Mọi schema/field mới phải khớp hợp đồng. Không tự nới gate hoặc tự chọn dung sai kỹ thuật.
Nếu thấy hợp đồng mâu thuẫn với code: ghi path/line và đề xuất; không tự sửa cả hệ thống.

CÁC BƯỚC:
1. git status --short; dừng phần ghi nếu checkout có thay đổi không thuộc task.
2. Đọc callers và tests trước khi sửa. Ghi danh sách tối đa 5 thay đổi dự định.
3. Implement phần nhỏ nhất đáp ứng task; tái sử dụng helpers hiện có.
4. Viết/chỉnh những test hành vi mà task chỉ định, dùng fixture tổng hợp.
5. Chạy <exact targeted test command do lead xác nhận>.
6. Đọc diff; kiểm không có dữ liệu riêng/secret; báo kết quả trung thực.
7. Nếu test/tool không chạy: ghi nguyên nhân và phần chưa kiểm. Không báo PASS.
8. Không đánh task hoàn tất nếu thiếu tiêu chí nghiệm thu bên dưới.

NGHIỆM THU:
<copy toàn bộ tests/acceptance của task và fixture IDs cụ thể>.

BÀN GIAO BẰNG MARKDOWN:
- Task và baseline SHA.
- Files changed và lý do từng file.
- Hành vi trước/sau, kèm tình huống lỗi.
- Tests: exact command, exit code, counts; skipped/failed riêng.
- Acceptance: mỗi tiêu chí PASS/FAIL/NOT_RUN + evidence.
- Contract deviations: none hoặc danh sách cần lead quyết định.
- Blockers và bước tiếp theo cụ thể.
- Chưa kiểm: giới hạn thật; không tự kết luận kỹ thuật công trình.
Không sửa task tracker thành DONE; lead nghiệm thu mới cập nhật.
```

## Ví dụ giao Q01 sau khi Q00 hoàn tất

Người điều phối điền contract digest, fixture directory và timeout đã chốt. Chỉ cho sửa `backend/app/services/audit_quality.py`, test tương ứng và config field đã duyệt. Không cho agent sửa runner/admin/worker trong Q01. Một test cần tạo fake validator chỉ để mô phỏng exit/timeout; không thay validator thực trong production. Bắt buộc có ít nhất một fixture chạy trusted bundle để chứng minh adapter tương thích schema thật.

## Mẫu lệnh kiểm tra môi trường — lead xác nhận trước khi giao

Từ thư mục backend của checkout, tạo venv riêng nếu chưa có:

```sh
python3 -m venv /tmp/boq-quality-task-venv
/tmp/boq-quality-task-venv/bin/python -m pip install -r requirements-dev.txt
PYTHONPATH=. /tmp/boq-quality-task-venv/bin/python -m pytest tests/test_audit_quality.py -q
```

Đây là lệnh dự kiến cho Q01 sau khi test file được tạo, chưa chạy trong phiên lập kế hoạch. Nếu dependency install thất bại, báo lỗi; không dùng môi trường production để giải quyết. Trước tích hợp, lead chạy suite CI thực tế `PYTHONPATH=. pytest -q` trong môi trường đã cài dependencies và ghi interpreter path.
