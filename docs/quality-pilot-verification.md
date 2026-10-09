# Kiểm chứng chuẩn bị pilot — 2026-10-08

Phạm vi: hoàn thiện worksheet/runbook theo kế hoạch đã duyệt và kiểm chứng cục bộ. Hai subagent thực hiện review tài liệu và kiểm thử contract backend; agent chính sửa tài liệu, chạy rehearsal synthetic và build frontend. Không có kết quả chất lượng BOQ hoặc quyết định readiness trong bản ghi này.

## Tài liệu và rehearsal synthetic

Đã sửa schema kết quả validator, quy tắc quan sát 5 finding hoặc tất cả nếu ít hơn, tiêu chí tự xác minh không được admin giúp, inventory snapshot/hash/owner, coverage theo bộ môn/lớp lỗi/mức độ, CLI provenance, effort và checklist UI/rehearsal. Snapshot gốc phải được giữ riêng trước retry; validation có bản copy mới và inventory riêng.

Rehearsal dùng thư mục tạm synthetic, Python 3.14.7 và validator cục bộ `boq-audit/scripts/validate_run.py` có SHA-256 `8bca7e846a4087c0ff27fc1f1834daa84e810a062325ea70d007d9feb1e0421b`. Harness lấy nguyên shell block preflight từ runbook, chạy bằng Bash, gọi validator bằng subprocess và assert kết quả; không chạy Codex/model.

Lệnh đã chạy: `python /tmp/boq-pilot-dev.WABiq6/verify_pilot.py`. Harness tạm được giữ trên máy này; không phải thành phần ứng dụng hoặc test framework mới. Các fixture synthetic được tự dọn sau mỗi lần chạy.

| Kiểm tra | Kết quả quan sát |
|---|---|
| Snapshot trước khi workspace được thay thế giả lập | Hash mọi tệp và trạng thái `FAILED` giữ nguyên trong snapshot |
| Validator nhận metadata không đủ trên copy mới | Exit 2, fresh JSON `BLOCKED`; schema file khác schema stdout, không có trường `status`/`report` trong file |
| Destination đã tồn tại và source chứa kết quả validator cũ | Preflight từ chối; destination cũ không bị sửa; validator chưa chạy |
| Source chứa symlink tới canary ngoài vùng | Từ chối trước copy/validator; không tạo destination |
| Source là symlink hoặc destination là dangling symlink | Từ chối trước copy; không tạo target của link |
| Source chứa FIFO | Từ chối trước copy; không tạo destination |
| Destination nằm trong source | Từ chối trước copy; không tạo destination |
| Nested `issue_id` là object thay vì ID | Validator exit 1 với `TypeError`, không có fresh result; phân loại `unavailable/blocked`, không suy ra verdict |

**Lỗi phát hiện và sửa:** `set -e` không dừng khi `test` bên trái `&&` thất bại; bản preflight cũ vẫn copy vào destination đã tồn tại. Tách từng `test` thành lệnh riêng và thêm guard destination nằm trong source. Tám nhóm assertion ở trên đều pass sau sửa. Đây là bằng chứng cho các trường hợp synthetic đã chạy, không chứng minh race/mount isolation hay độ đúng kỹ thuật.

## Regression ứng dụng

- Frontend: `npm run typecheck` và `npm run build` đều exit 0; Next.js tạo đủ các route hiện có. Không suy ra rendered accessibility hoặc usability từ build.
- Backend targeted: 24 tests pass trong 4.39 giây (`test_auth.py`, `test_timing_chat.py`, `test_chat_gateway.py`); full suite: **57 tests pass trong 9.70 giây**, exit 0. Python 3.12.15 trong venv tạm với dependency từ `backend/requirements-dev.txt`.
- Lệnh targeted từ `backend/`: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. TELEGRAM_BOT_TOKEN= TELEGRAM_CHAT_ID= CHAT_GATEWAY_TOKEN= timeout 120s /tmp/boq-pilot-dev.WABiq6/venv/bin/python -m pytest -q -p no:cacheprovider tests/test_auth.py tests/test_timing_chat.py tests/test_chat_gateway.py`. Fixtures dùng DB/files trong `/tmp`, mock audit và gateway; không gọi model hoặc dịch vụ thật.
- Lệnh full suite: cùng environment/prefix ở trên, bỏ danh sách ba tệp test, giữ `-q -p no:cacheprovider`.
- Python hệ thống thiếu pytest; cài dependency trong sandbox gặp lỗi DNS, cài vào venv tạm với network access thành công. Test tạo job trong sandbox bị treo ở AnyIO/event loop; cùng test chạy ngoài sandbox pass (1 test, 0.34 giây), targeted suite chạy ngoài sandbox pass. Không sửa ứng dụng để né giới hạn môi trường này.

## Việc còn cần bằng chứng

- Bộ ba hồ sơ synthetic/ẩn danh đã duyệt, nhãn tham chiếu độc lập, scope/tolerance và người thẩm tra phù hợp.
- Môi trường test riêng cùng canary chứng minh ranh giới filesystem/credential theo job; chưa kiểm chứng worker/deployment thật.
- Chạy model/pilot chỉ sau khi đủ điều kiện; ghi mọi attempt và adjudicate cả findings lẫn narrative.
- Quan sát kiến trúc sư và người mới đọc tài liệu; rendered UI/screen-reader/viewport checks vẫn chưa thực hiện.

Giữ pilot ở trạng thái chưa đánh giá cho đến khi có bằng chứng tương ứng; không dùng rehearsal synthetic hay regression phần mềm để tuyên bố độ chính xác BOQ.
