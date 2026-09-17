# BOQ Audit Portal V1 — Implementation plan

1. Xây dựng FastAPI backend với SQLAlchemy/SQLite, JWT trong HTTP-only cookie, seed tài khoản từ biến môi trường và kiểm tra role/ownership tại mọi endpoint.
2. Cài đặt vòng đời hồ sơ, upload/download theo luồng, whitelist định dạng/kích thước, lưu file ngoài database và không lộ đường dẫn vật lý.
3. Viết test tích hợp cho authentication, IDOR, admin authorization, upload/download, hoàn thành hồ sơ, path traversal, sai định dạng và quá dung lượng.
4. Xây dựng Next.js App Router UI tiếng Việt cho customer/admin, responsive, có upload progress, trạng thái thực và xử lý lỗi/session.
5. Đóng gói frontend/backend/Caddy bằng Docker Compose với volume dữ liệu bền vững.
6. Chạy backend tests, frontend lint/type/build và kiểm tra end-to-end qua Docker; sửa lỗi phát hiện được.
7. Hoàn thiện scripts backup/restore và README vận hành trên Linux/CachyOS.

