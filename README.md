# BOQ Audit Portal V1

Cổng web tối giản cho khách hàng gửi PDF hồ sơ BOQ, theo dõi trạng thái và nhận báo cáo Excel/PDF đánh dấu. Việc tra soát chuyên môn ở V1 được admin thực hiện thủ công ngoài hệ thống; ứng dụng chỉ quản lý quy trình và tệp.

## Kiến trúc

- Next.js App Router + TypeScript + Tailwind CSS: giao diện customer/admin.
- FastAPI + SQLAlchemy + SQLite: API, authentication, authorization và nghiệp vụ job.
- Local filesystem: file nằm trong `data/jobs`, không được Caddy public trực tiếp.
- Caddy: reverse proxy duy nhất ở cổng 80/443.
- Docker Compose: ba service `frontend`, `backend`, `caddy`.

Frontend chỉ gọi REST API. Nghiệp vụ trạng thái, ownership, file output và điều kiện hoàn thành nằm hoàn toàn ở backend, nên V2/V3 có thể thêm worker mà không đổi giao diện.

## Development

### Chạy nhanh trên CachyOS / Arch Linux

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

Lần khởi động đầu tiên backend tạo admin từ `ADMIN_*`. Customer demo chỉ được tạo khi `DEMO_PASSWORD` được đặt rõ ràng; để trống thì không seed demo (đây là mặc định production). Tên đăng nhập là chuỗi không có khoảng trắng, không cần là email. Seed không ghi đè tài khoản đã tồn tại.

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

Script dùng SQLite backup API để snapshot database an toàn cả khi WAL đang bật, sau đó archive snapshot cùng `data/jobs`. Để database và filesystem khớp tuyệt đối trong cùng một thời điểm, vẫn nên tạm dừng ghi dữ liệu:

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
- `GET|POST /api/jobs`, `GET|PATCH|DELETE /api/jobs/{job_code}`
- `GET /api/jobs/{job_code}/input/download`
- `GET /api/jobs/{job_code}/outputs/{output_id}/download`
- `GET /api/jobs/{job_code}/outputs/{output_id}/view`
- `GET /api/admin/jobs`, `GET|PATCH /api/admin/jobs/{job_code}`
- `GET /api/admin/jobs/{job_code}/input/download`
- `POST /api/admin/jobs/{job_code}/outputs`
- `DELETE /api/admin/jobs/{job_code}/outputs/{output_id}`
- `DELETE /api/admin/jobs/{job_code}`
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

## Production trên Google Compute Engine

Mục tiêu được hỗ trợ là một VM Ubuntu 24.04 (ví dụ `e2-medium`) chạy một backend instance bằng Docker Compose. Chuẩn bị VM, trỏ DNS A/AAAA của domain vào external IP tĩnh và mở ingress TCP 80/443. Clone repository, sau đó:

```bash
cp .env.production.example .env
openssl rand -hex 32
```

Đưa secret vừa sinh vào `.env`, đặt mật khẩu admin mạnh (tối thiểu 12 ký tự), email/domain thật và giữ `APP_ENV=production`, `COOKIE_SECURE=true`. Production sẽ fail-fast nếu secret yếu/ngắn, mật khẩu admin yếu/ngắn hoặc cookie không Secure. Không đặt `DEMO_PASSWORD` nếu không chủ ý tạo customer demo.

Development dùng `Caddyfile` mặc định với HTTP localhost. Trên production, đổi site address sang domain và bỏ global option `auto_https off`. Ví dụ tương đương:

```caddy
boq.example.com {
    encode zstd gzip

    header {
        X-Content-Type-Options nosniff
        Referrer-Policy strict-origin-when-cross-origin
        Permissions-Policy "camera=(), microphone=(), geolocation=()"
        -Server
    }

    @notPdfViewer not path /api/jobs/*/input/view /api/jobs/*/outputs/*/view
    header @notPdfViewer X-Frame-Options DENY

    @uploads {
        method POST
        path /api/jobs /api/admin/jobs/*/outputs
    }
    request_body @uploads {
        max_size 510MB
    }

    @api path /api/*
    handle @api {
        reverse_proxy backend:8000
    }
    handle {
        reverse_proxy frontend:3000
    }
}
```

`510MB` chừa multipart overhead cho `MAX_UPLOAD_MB=500`; nếu đổi application limit, cập nhật proxy limit tương ứng. Caddy tự cấp và gia hạn HTTPS. Khởi động và kiểm tra:

```bash
docker compose config
docker compose up -d --build
docker compose ps
curl -fsS https://boq.example.com/api/health
```

Healthcheck chỉ healthy khi ứng dụng chạy, SQLite truy cập được và `DATA_DIR` tồn tại/có thể ghi. Log của ba service được xoay ở 10 MB × 3 file.

### Dữ liệu và quyền container

`./data:/data` là bind mount chứa cả `database/boq.db` và `jobs/`, nên `docker compose down`/rebuild không xóa dữ liệu. Hãy backup `./data` trước nâng cấp và không xóa thư mục này. Frontend đã chạy non-root; image Caddy chính thức tự quản lý privilege. Backend hiện giữ user mặc định của image để tương thích quyền sở hữu của bind mount `/data` trên các máy V1 hiện hữu. Chuyển thẳng sang UID cố định có thể làm production cũ mất quyền đọc/ghi; nên thực hiện sau khi có migration ownership/entrypoint kiểm soát rõ, không dùng `chmod 777`.

### Cấu hình production HTTPS

Đặt `FRONTEND_URL=https://boq.example.com`; backend chỉ cho phép CORS từ origin này. Không dùng `auto_https off` trong production.

## Bảo mật đã áp dụng

- Mật khẩu Argon2; JWT hết hạn trong cookie HTTP-only, SameSite Strict và hỗ trợ Secure.
- Backend kiểm role và ownership; truy vấn job customer luôn ràng buộc `user_id` để chặn IDOR.
- Customer chỉ nhận metadata/download/preview output khi job đang `COMPLETED`; chuyển lại `REVIEW` thu hồi quyền ngay. Admin vẫn xem được output nháp.
- Mọi download đi qua API authorization và streaming response; không public `/data`.
- Filename được lấy basename, chuẩn hóa; đường dẫn lưu sinh ngẫu nhiên, không ghép path từ request.
- Kiểm extension, MIME, magic bytes, file rỗng và giới hạn kích thước trong lúc stream.
- Upload đọc theo chunk 1 MiB qua API bất đồng bộ của `UploadFile`; transaction SQLite tạo job kết thúc trước khi copy file lớn.
- SQLite dùng WAL, foreign keys và busy timeout 5 giây; login giới hạn 5 lần thất bại/phút/IP trong memory của single backend.
- Output chỉ hoàn thành khi đủ Excel và annotated PDF; ghi chú nội bộ tách khỏi ghi chú khách hàng.
- Không log token, cookie, password hay nội dung PDF; response không lộ hash/path/stack trace.

## Giới hạn thực tế V1

- SQLite phù hợp một backend instance và tải MVP; không chạy nhiều replica ghi đồng thời.
- MIME/magic-byte validation ngăn lỗi phổ biến nhưng không thay thế malware scanning chuyên dụng.
- Chưa có self-registration, email notification, audit-history bất biến hay quy trình quên mật khẩu; tài khoản do admin tạo bằng CLI.
- Không có xử lý tự động nội dung hồ sơ. Điểm mở rộng dự kiến là worker gọi cùng lớp dữ liệu/API để nhận input và tạo output.
