# Cổng công cụ và Trợ giúp vẽ CAD (`/cad/`)

Portal gộp hai công cụ sau một lần đăng nhập: trang chọn công cụ `/tools` và Trợ giúp vẽ
(SBTech CAD Studio, repository `CAD-Agent-Demo`) chạy dưới `/cad/` trên cùng domain. CAD giữ
nguyên thiết kế không-login; Caddy của portal kiểm tra phiên đăng nhập trước khi chuyển tiếp.
Mọi kết quả CAD là bản nháp kỹ thuật, cần người có chuyên môn kiểm tra trước khi sử dụng.

## Kiến trúc

- `GET /api/auth/me` là điểm kiểm tra phiên; mọi tài khoản đã đăng nhập đều dùng được CAD.
- Caddy tham gia thêm mạng `sbtech-shared`; stack CAD (`compose.portal.yaml`) tham gia cùng
  mạng dưới tên `sbtech-cad`; Caddy bỏ tiền tố `/cad` rồi chuyển tiếp tới `sbtech-cad:4173`.
- Không thêm port hay firewall: vẫn chỉ cổng 80/443 như trước.

## Cấu hình

Khối `/cad/` trong `Caddyfile` của repo là nguồn chuẩn cho local. Trên VM production, Caddyfile
do người vận hành sửa tay (CI không chép đè Caddyfile): thêm nguyên khối `/cad/` vào site block
production, trước `handle` mặc định, rồi nạp lại:

```bash
cd /home/hello291994/BOQ-Audit
sudo docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile
sudo docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```

Với local, Caddy nghe cổng 80 nên trình duyệt gửi `Host` **không kèm cổng** (`localhost`).
Allow-list của CAD khớp tuyệt đối, nên tạo `.env` trong repo CAD (tệp đã nằm trong
`.gitignore`) trước khi `up -d`, giữ cả dạng qua portal lẫn khi mở trực tiếp cổng 4173:

```bash
SB_CAD_ALLOWED_HOSTS=localhost,127.0.0.1,localhost:4173,127.0.0.1:4173
SB_CAD_ALLOWED_ORIGINS=http://localhost,http://127.0.0.1,http://localhost:4173,http://127.0.0.1:4173
```

Stack CAD trên VM (sau khi mã nguồn CAD đã nằm tại `/home/hello291994/CAD-Agent-Demo` và
OpenCode state đã được cài):

```bash
sudo docker network create sbtech-shared          # nếu chưa có
cd /home/hello291994/CAD-Agent-Demo
sudo SB_CAD_ALLOWED_HOSTS=sbtech-boq.duckdns.org \
     SB_CAD_ALLOWED_ORIGINS=https://sbtech-boq.duckdns.org \
     docker compose -f compose.yaml -f compose.portal.yaml up -d
```

## Kiểm tra

```bash
curl -sI https://sbtech-boq.duckdns.org/cad/ | head -2   # chưa đăng nhập → 303 /login
```

Sau khi đăng nhập: `/tools` → «Trợ giúp vẽ» → giao diện CAD Studio hiện dưới `/cad/`; chạy một
job thật để kiểm chứng quyền model và luồng tải tệp.

## Rollback

- Gỡ khối `/cad/` khỏi Caddyfile production + `caddy reload` → portal về trạng thái cũ ngay.
- Dừng stack CAD bằng compose trong repo CAD; dữ liệu `drawings/` giữ nguyên.
- Trang `/tools` vẫn hoạt động khi CAD tắt (thẻ CAD chỉ báo lỗi kết nối).
