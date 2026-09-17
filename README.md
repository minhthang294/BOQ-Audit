# BOQ Audit Portal V1

Cổng web tối giản cho khách hàng gửi PDF hồ sơ BOQ, theo dõi trạng thái và nhận báo cáo Excel/PDF đánh dấu. Việc tra soát chuyên môn ở V1 được admin thực hiện thủ công ngoài hệ thống; ứng dụng chỉ quản lý quy trình và tệp.

## Kiến trúc

- Next.js App Router + TypeScript + Tailwind CSS: giao diện customer/admin.
- FastAPI + SQLAlchemy + SQLite: API, authentication, authorization và nghiệp vụ job.
- Local filesystem: file nằm trong `data/jobs`, không được Caddy public trực tiếp.
- Caddy: reverse proxy duy nhất ở cổng 80/443.
- Docker Compose: ba service `frontend`, `backend`, `caddy`.

Frontend chỉ gọi REST API. Nghiệp vụ trạng thái, ownership, file output và điều kiện hoàn thành nằm hoàn toàn ở backend, nên V2/V3 có thể thêm worker mà không đổi giao diện.

## Chạy nhanh trên CachyOS / Arch Linux

Cài Docker nếu máy chưa có:

```bash
sudo pacman -S --needed docker docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Đăng xuất/đăng nhập lại sau lệnh `usermod`, rồi tại repository:

```bash
cp .env.example .env
openssl rand -hex 32
```

Đưa chuỗi vừa sinh vào `BACKEND_SECRET_KEY`, đổi toàn bộ password trong `.env`, sau đó:

```bash
docker compose up -d --build
```

Mở `http://localhost`. Không dùng password mẫu trong môi trường có người dùng thật.

## Tài khoản

Lần khởi động đầu tiên backend tạo admin/customer demo từ `ADMIN_*` và `DEMO_*` trong `.env`. Tên đăng nhập là chuỗi không có khoảng trắng, không cần là email. Seed không ghi đè tài khoản đã tồn tại.

Tạo customer mới:

```bash
docker compose exec backend python -m app.cli create-user --name "Nguyễn Văn A" --username nguyenvana --password 'mat-khau-manh'
```

Tạo thêm admin bằng cách thêm `--role ADMIN`. Đổi password admin hiện hữu:

```bash
docker compose exec backend python -m app.cli set-password --username admin --password 'mat-khau-moi-rat-manh'
```

## Vận hành

```bash
# Xem trạng thái và logs
docker compose ps
docker compose logs -f --tail=200

# Dừng / chạy lại / rebuild
docker compose down
docker compose up -d
docker compose up -d --build
```

Dữ liệu được bind mount ở `./data`, nên `docker compose down` hoặc rebuild không xóa database/file. Không chạy `docker compose down -v` nếu không muốn xóa volume cấu hình Caddy.

### Backup và restore

Backup nhất quán nhất khi tạm dừng ghi dữ liệu:

```bash
docker compose stop backend
./scripts/backup.sh
docker compose start backend
```

Restore giữ lại dữ liệu hiện tại dưới tên `data.before-restore-<timestamp>`:

```bash
docker compose down
./scripts/restore.sh backups/boq-backup-2026-09-17-001500.tar.gz
docker compose up -d
```

## API chính

- `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me`
- `GET|POST /api/jobs`, `GET /api/jobs/{job_code}`
- `GET /api/jobs/{job_code}/input/download`
- `GET /api/jobs/{job_code}/outputs/{output_id}/download`
- `GET /api/admin/jobs`, `GET|PATCH /api/admin/jobs/{job_code}`
- `GET /api/admin/jobs/{job_code}/input/download`
- `POST /api/admin/jobs/{job_code}/outputs`
- `DELETE /api/admin/jobs/{job_code}/outputs/{output_id}`
- `POST /api/admin/jobs/{job_code}/complete`

Swagger có tại `/api/docs` khi `APP_ENV` không phải `production`.

## Kiểm thử cục bộ

Backend:

```bash
python -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
cd backend && PYTHONPATH=. ../.venv/bin/pytest -q
```

Frontend:

```bash
cd frontend
npm install
npm run typecheck
npm run build
```

## Cấu hình production HTTPS

Trên server có domain, đổi dòng `:80` trong `Caddyfile` thành domain thật (ví dụ `boq.example.com`), bỏ khối `auto_https off`, đặt `FRONTEND_URL=https://boq.example.com` và `COOKIE_SECURE=true`. Mở cổng 80/443 trên firewall. Caddy sẽ cấp/chuyển hạn chứng chỉ tự động.

## Bảo mật đã áp dụng

- Mật khẩu Argon2; JWT hết hạn trong cookie HTTP-only, SameSite Strict và hỗ trợ Secure.
- Backend kiểm role và ownership; truy vấn job customer luôn ràng buộc `user_id` để chặn IDOR.
- Mọi download đi qua API authorization và `FileResponse` streaming; không public `/data`.
- Filename được lấy basename, chuẩn hóa; đường dẫn lưu sinh ngẫu nhiên, không ghép path từ request.
- Kiểm extension, MIME, magic bytes, file rỗng và giới hạn kích thước trong lúc stream.
- Output chỉ hoàn thành khi đủ Excel và annotated PDF; ghi chú nội bộ tách khỏi ghi chú khách hàng.
- Không log token, cookie, password hay nội dung PDF; response không lộ hash/path/stack trace.

## Giới hạn thực tế V1

- SQLite phù hợp một backend instance và tải MVP; không chạy nhiều replica ghi đồng thời.
- MIME/magic-byte validation ngăn lỗi phổ biến nhưng không thay thế malware scanning chuyên dụng.
- Chưa có self-registration, email notification, audit-history bất biến hay quy trình quên mật khẩu; tài khoản do admin tạo bằng CLI.
- Không có xử lý tự động nội dung hồ sơ. Điểm mở rộng dự kiến là worker gọi cùng lớp dữ liệu/API để nhận input và tạo output.
