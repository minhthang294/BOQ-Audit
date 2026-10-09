# CI/CD Google Compute Engine

## Luồng

- Pull request: backend `pytest`, frontend typecheck/build, kiểm tra deploy guard/rollback và build thử ba Docker image. Không dùng thông tin xác thực GCP.
- Build frontend Docker từng báo npm advisory (11, gồm 1 critical); `npm audit` trên máy dev không kiểm chứng được do DNS tới registry lỗi. Cần xem lại advisory trên GitHub/npm trước khi đặt security scan làm cổng deploy.
- Push lên `main`: các kiểm tra trên phải đạt; GitHub Actions build ba image (`backend`, `frontend`, `chat`) với tag duy nhất `<commit-sha>-<run-id>-<attempt>` rồi đẩy lên Artifact Registry.
- Deploy: job `production` chép Compose/script lên VM rồi pull đúng SHA, chờ health checks. Nếu có audit đang chờ/chạy, deploy dừng để không làm worker hiện tại bị ngắt. Nếu lần deploy mới không khỏe, script gắn lại image cũ đang chạy và rollback. Chat gateway là tùy chọn; khi đặt `CHAT_GATEWAY_TOKEN` đủ 32 ký tự trong `.env`, deploy tự bật profile và chép riêng `auth.json` vào volume giới hạn của Chat.
- Chạy **Actions → CI/CD → Run workflow** trên `main` với `image_tag` trống để phát hành `main`; nhập release tag đã deploy để rollback thủ công.

Ứng dụng vẫn là một VM/Compose/SQLite. Không deploy nhiều backend replica. Job deploy sẽ restart backend; guard giảm nguy cơ gián đoạn audit nhưng không thay thế maintenance window nếu cần bảo đảm tuyệt đối không có upload mới đúng lúc deploy. Migrations hiện tại chỉ thêm bảng; nếu sau này có migration phá vỡ tương thích, cần backup/restore và rollout riêng, vì rollback image không đảo ngược database.

## GitHub repository variables

Tạo các Actions Variables sau; đây không phải secrets:

| Variable | Giá trị |
| --- | --- |
| `GCP_PROJECT_ID` | Project chứa Artifact Registry |
| `GCP_REGION` | Region của Artifact Registry, ví dụ `asia-southeast1` |
| `AR_REPOSITORY` | Docker repository, ví dụ `boq-audit` |
| `GCP_WIF_PROVIDER` | Resource name `projects/NUMBER/locations/global/workloadIdentityPools/POOL/providers/PROVIDER` |
| `GCP_BUILD_SERVICE_ACCOUNT` | Email service account chỉ có quyền push image |
| `GCP_DEPLOY_SERVICE_ACCOUNT` | Email service account chỉ dùng để vào VM và deploy |
| `GCE_PROJECT_ID` | Project có VM; có thể bỏ trống nếu trùng `GCP_PROJECT_ID` |
| `GCE_INSTANCE` | Tên Compute Engine instance |
| `GCE_ZONE` | Zone của VM |
| `GCE_DEPLOY_PATH` | Thư mục Compose có `.env` và `data`; mặc định `/home/hello291994/BOQ-Audit` |

Trong **Settings → Environments**, tạo environment `production`, giới hạn deployment branch là `main`, và đặt required reviewer. Nếu repo private, khả năng required reviewer phụ thuộc gói GitHub; nếu không có, dùng phê duyệt thủ công theo quy trình ngoài GitHub trước khi merge/deploy.

## Google Cloud chuẩn bị một lần

Ví dụ dưới đây dùng Cloud Shell/`gcloud` và GitHub CLI đã đăng nhập. Chọn đúng project/region/zone trước khi chạy; repository có thể đã được tạo thì bỏ qua lệnh create:

```bash
export GCP_PROJECT_ID="your-gcp-project"
export GCP_REGION="asia-southeast1"
export AR_REPOSITORY="boq-audit"
export GCE_PROJECT_ID="$GCP_PROJECT_ID"
export GCE_INSTANCE="your-vm-name"
export GCE_ZONE="asia-southeast1-b"
export POOL="github-actions"
export PROVIDER="boq-audit"
export BUILD_SA="boq-ci-build@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
export DEPLOY_SA="boq-ci-deploy@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
PROJECT_NUMBER="$(gcloud projects describe "$GCP_PROJECT_ID" --format='value(projectNumber)')"
REPOSITORY_ID="$(gh api repos/minhthang294/BOQ-Audit --jq .id)"
OWNER_ID="$(gh api repos/minhthang294/BOQ-Audit --jq .owner.id)"

gcloud services enable artifactregistry.googleapis.com iamcredentials.googleapis.com sts.googleapis.com compute.googleapis.com iap.googleapis.com --project "$GCP_PROJECT_ID"
gcloud artifacts repositories create "$AR_REPOSITORY" --project "$GCP_PROJECT_ID" --location "$GCP_REGION" --repository-format docker --description="BOQ Audit release images"
gcloud artifacts repositories update "$AR_REPOSITORY" --project "$GCP_PROJECT_ID" --location "$GCP_REGION" --immutable-tags
gcloud iam service-accounts create boq-ci-build --project "$GCP_PROJECT_ID"
gcloud iam service-accounts create boq-ci-deploy --project "$GCP_PROJECT_ID"
gcloud iam workload-identity-pools create "$POOL" --project "$GCP_PROJECT_ID" --location global --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --project "$GCP_PROJECT_ID" --location global --workload-identity-pool "$POOL" --display-name="BOQ Audit main branch" --issuer-uri="https://token.actions.githubusercontent.com/" --attribute-mapping="google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.repository_owner_id=assertion.repository_owner_id,attribute.ref=assertion.ref" --attribute-condition="assertion.repository_id == '${REPOSITORY_ID}' && assertion.repository_owner_id == '${OWNER_ID}' && assertion.ref == 'refs/heads/main'"

FEDERATED_PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository_id/${REPOSITORY_ID}"
for service_account in "$BUILD_SA" "$DEPLOY_SA"; do
  gcloud iam service-accounts add-iam-policy-binding "$service_account" --project "$GCP_PROJECT_ID" --role roles/iam.workloadIdentityUser --member "$FEDERATED_PRINCIPAL"
done

gcloud artifacts repositories add-iam-policy-binding "$AR_REPOSITORY" --project "$GCP_PROJECT_ID" --location "$GCP_REGION" --member="serviceAccount:${BUILD_SA}" --role=roles/artifactregistry.writer
```

Gán quyền deploy cho đúng VM. Các lệnh `add-iam-policy-binding` tạo quyền rộng; kiểm tra service account đang gắn vào VM trước, rồi cấp role ở mức resource hẹp nhất có thể:

```bash
VM_SA="$(gcloud compute instances describe "$GCE_INSTANCE" --project "$GCE_PROJECT_ID" --zone "$GCE_ZONE" --format='value(serviceAccounts[0].email)')"
gcloud projects add-iam-policy-binding "$GCE_PROJECT_ID" --member="serviceAccount:${DEPLOY_SA}" --role=roles/compute.viewer
gcloud compute instances add-iam-policy-binding "$GCE_INSTANCE" --project "$GCE_PROJECT_ID" --zone "$GCE_ZONE" --member="serviceAccount:${DEPLOY_SA}" --role=roles/compute.osAdminLogin
gcloud artifacts repositories add-iam-policy-binding "$AR_REPOSITORY" --project "$GCP_PROJECT_ID" --location "$GCP_REGION" --member="serviceAccount:${VM_SA}" --role=roles/artifactregistry.reader
```

Gán `roles/iap.tunnelResourceAccessor` ở đúng VM, không phải cấp project: mở **Security → Identity-Aware Proxy → SSH and TCP Resources**, chọn instance rồi thêm deploy SA. Có thể giới hạn binding bằng IAM Condition `destination.port == 22`.

Trước khi cấp `roles/iam.serviceAccountUser` cho deploy SA hoặc bật OS Login, kiểm tra mọi role của service account đang gắn vào VM:

```bash
gcloud projects get-iam-policy "$GCE_PROJECT_ID" --flatten='bindings[].members' --filter="bindings.members:serviceAccount:${VM_SA}" --format='value(bindings.role)'
```

SSH với OS Admin cho phép CI chạy lệnh root và lấy credential của runtime service account từ metadata server. Nếu policy có `roles/editor`, `roles/owner` hoặc quyền rộng tương tự, **dừng ở đây**: không cấp `roles/iam.serviceAccountUser` và không bật OS Login cho CI. Hãy tạo runtime service account riêng với đúng quyền ứng dụng cần, gắn nó vào VM trong maintenance window đã duyệt, rồi mới cấp `roles/iam.serviceAccountUser` cho deploy SA trên service account tối thiểu đó. Tài khoản runtime hiện tại của VM `boq-audit` có `roles/editor` ở cấp project nên chưa an toàn để cấp quyền SSH cho CI.

Khi runtime service account đã được thu hẹp, cấp quyền OS Login trên instance và quyền `roles/iam.serviceAccountUser` trên service account đó. Chỉ bật OS Login sau khi đã xác minh người đang dùng SSH key metadata và chuẩn bị chuyển họ sang OS Login; khi bật, VM bỏ qua SSH key trong metadata. Firewall SSH cần giới hạn TCP/22 vào dải IAP `35.235.240.0/20`; project hiện có rule `default-allow-ssh` từ `0.0.0.0/0`, nên SSH trực tiếp hiện vẫn mở rộng hơn IAP. Không xóa rule đó trước khi kiểm tra tác động lên các VM khác trong project. Xác nhận scope VM có `storage-ro` hoặc `cloud-platform`; không thay scope của VM đang chạy nếu chưa lên kế hoạch dừng máy.

Mapping và condition cho GitHub OIDC khóa theo ID số duy nhất của repository/owner và nhánh `main`:

```text
google.subject=assertion.sub
attribute.repository_id=assertion.repository_id
attribute.repository_owner_id=assertion.repository_owner_id
attribute.ref=assertion.ref
```

```text
assertion.repository_id == '<REPOSITORY_ID>' && assertion.repository_owner_id == '<OWNER_ID>' && assertion.ref == 'refs/heads/main'
```

Cho phép principal theo `attribute.repository_id/<REPOSITORY_ID>` impersonate cả hai service account bằng `roles/iam.workloadIdentityUser`. Không tạo hoặc lưu JSON key dài hạn. Hai job CI/CD tách build-SA và deploy-SA để quyền push Artifact Registry không đi kèm quyền đăng nhập VM. GitHub Actions dùng OIDC để nhận credential ngắn hạn theo khuyến nghị của Google Cloud.

VM cần:

1. OS Login bật; deploy SA có quyền OS Admin Login trên instance. Role này cho deploy SA sudo/root trên VM. Vì VM có service account gắn kèm, OS Login cũng yêu cầu `roles/iam.serviceAccountUser` trên service account đó; giữ runtime service account ở quyền tối thiểu. IAP TCP forwarding phải được bật và firewall chỉ cho TCP/22 từ dải IAP `35.235.240.0/20`. Không cấp `iam.serviceAccountUser` nếu VM đang dùng service account `Editor`/`Owner`.
2. Docker Compose v2, `curl`, `gcloud` và app directory do bạn chọn. Thư mục có `.env` production, Caddyfile production/domain, `data/` hiện hữu và quyền đọc/ghi phù hợp. Workflow chỉ cập nhật `docker-compose.yml`, deploy script và `.deploy.env`; nó không ghi đè `.env`, dữ liệu hoặc Caddyfile.
3. VM service account có quyền `roles/artifactregistry.reader` trên repository và scope `storage-ro` hoặc `cloud-platform`. Cấu hình Docker helper cho root, vì workflow chạy Compose bằng `sudo`:

```bash
sudo gcloud auth configure-docker asia-southeast1-docker.pkg.dev --quiet
```

Lệnh trên giả định gcloud trong VM dùng service account đính kèm VM. Xác nhận pull thử một image trước khi bật CI/CD. Không cấp Artifact Registry Writer cho VM.

Bảo vệ `main` bằng PR bắt buộc và hai check `Tests`, `Build container images (PR)`; khi repo chỉ có một collaborator, để số review bắt buộc bằng 0 để không khóa merge. Khi có thêm người duyệt, yêu cầu ít nhất một review độc lập. Environment `production` cần reviewer và chỉ nhận branch `main`.

Chỉ chạy workflow lần đầu sau khi runtime service account đã được thu hẹp, các SSH user đã được chuyển an toàn sang OS Login, IAP/SSH ingress đã giới hạn đúng, và Docker helper đã được cấu hình. Hiện tại chưa bật OS Login hoặc cấp `roles/iam.serviceAccountUser` cho deploy SA vì VM đang dùng runtime account có `roles/editor`; không chạy workflow deploy cho tới khi xử lý xong điểm này. Sau khi điều kiện an toàn đạt, push/merge vào `main` sẽ tự phát hành.

## Dữ liệu, rollback và vận hành

- Các image gắn tag `<full-Git-SHA>-<run-id>-<attempt>`, không dùng `latest`; repository bật immutable tags để tag không thể bị trỏ sang digest khác. Giữ image đã phát hành trong Artifact Registry đủ lâu để rollback. Script giữ image của service đang chạy bằng local `rollback` tag trước khi đổi phiên bản.
- Pipeline không chạy `docker compose down -v`, không sửa `.env`, `data/`, database/file hay Caddyfile. Vẫn duy trì backup/snapshot cho persistent disk; deploy không thay thế backup.
- Backend startup xử lý các job đang `SUBMITTED`/`PROCESSING` thành `FAILED`, nên guard sẽ từ chối deploy khi thấy job/audit đang chạy. Nếu deploy bị chặn, chờ xong rồi rerun workflow; không xóa trạng thái job để ép deploy.
- Khi health check fail, workflow trả fail sau khi thử rollback image hiện tại. Nếu đây là lần phát hành đầu tiên và không có container cũ để giữ, phải kiểm tra logs trên VM thủ công.
- Theo dõi dung lượng disk: Docker giữ image cũ phục vụ rollback; chưa bật tự xóa image trên VM hay cleanup policy Artifact Registry để tránh xóa nhầm image cần khôi phục.
