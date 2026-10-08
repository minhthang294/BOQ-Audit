# Admin runbook: pilot chất lượng BOQ

**Đối tượng:** admin và người kiểm tra BOQ có chuyên môn. Đây là quy trình đánh giá nội bộ có giới hạn. `COMPLETED` chỉ mô tả tiến trình/tệp đầu ra; nó không xác nhận báo cáo đúng, đầy đủ hay an toàn để phát hành.

> **SYNTHETIC · NOT PILOT DATA.** Ví dụ trong runbook và worksheet là hư cấu để dạy cách adjudicate/tính số. Không nhập vào corpus, không đưa ra như kết quả thật.

## Tutorial: chuẩn bị và chạy pilot

### 1. Chốt câu hỏi và vai trò

Viết trước hạng mục cần biết, phạm vi dự kiến, tiêu chí pass/fail từng nhiệm vụ, chủ hồ sơ và người adjudicate bất đồng. Người kiểm tra độc lập tạo nhãn tham chiếu ngoài đầu vào model. Admin sở hữu runbook, snapshot và quyết định phạm vi. Kiến trúc sư tham gia chỉ thực hiện bài quan sát với báo cáo thông thường.

Không đặt một mặc định dung sai cho mọi loại khối lượng. Mỗi quy tắc hình học, đơn vị, giả định, tolerance và mức độ phải do người có chuyên môn phê duyệt cho phạm vi cụ thể. Không biến kết quả pilot thành quyết định nghiệm thu hoặc phát hành hồ sơ.

### 2. Chọn bộ hồ sơ và nhãn độc lập

Chọn tối thiểu ba gói đại diện đã được duyệt: (1) có lỗi trong phạm vi đã kiểm chứng, (2) scoped-clean đã được người có chuyên môn kiểm tra, (3) có chứng cứ mơ hồ/không đủ. Gói thứ nhất chỉ là rehearsal và kiểm tra phụ thuộc; chỉ sau khi có đủ cả bộ mới xem xét readiness. Nếu không có một loại mẫu, ghi rõ hạn chế và chưa tuyên bố đủ coverage.

Đóng băng revision, nguồn và phạm vi từng gói. Lập nhãn tham chiếu riêng trước khi xem báo cáo model; ghi ID lỗi, BOQ row/sheet/cell, bản vẽ và PDF page + số hiệu in, đối tượng/vị trí, lớp lỗi/mức độ, phép tính/cơ sở hình học, đơn vị, assumptions, tolerance đã duyệt, người adjudicate và hash của tệp nguồn. Giữ lại nhãn gốc. Phát hiện mới chỉ thành reference sau khi adjudicate độc lập; tăng phiên bản thay vì sửa kết quả cũ. Dữ liệu tổng hợp phải có nhãn riêng và không bao giờ nhập vào mẫu số dự án.

### 3. Kiểm tra môi trường trước mọi lần chạy trực tiếp

Đây là cổng bắt buộc, không thể suy ra từ việc prompt, thư mục làm việc hay lệnh model nói “chỉ đọc”. Trước khi gửi bất kỳ tệp pilot nào vào worker, admin phải có bằng chứng cho từng job rằng:

- Mỗi lần đánh giá trực tiếp chỉ dùng đầu vào synthetic hoặc đã ẩn danh được duyệt trong môi trường test riêng; không mount dữ liệu/config production. Môi trường và quyền truy cập phải được duyệt cho đúng dữ liệu pilot.
- Worker/job có ranh giới filesystem thực thi được. Kiểm tra bằng canary tổng hợp rằng nó chỉ đọc được vùng input được phép, không đọc được file thử nghiệm ngoài vùng đó (kể cả credential/config mẫu), và không ghi ra vùng ngoài output được phép. Không đưa secret vào canary hay ghi nội dung nhạy cảm vào log.
- Các mount, identity/credential, quyền user và log của job đã được người chịu trách nhiệm xem xét; lưu kết quả kiểm chứng/redacted evidence cùng revision môi trường.

Trong cấu hình Docker Compose đã xem, worker dùng chung volume `/data` và Codex home được mount vào backend; ranh giới truy cập job chưa được chứng minh. Nếu admin không chứng minh được cô lập cho đúng cấu hình đang dùng, **không chạy trực tiếp**. Chỉ có thể đánh giá offline các artifact đã được duyệt, giữ nguyên provenance là `không biết/không có`, và không diễn giải như benchmark cấu hình hiện tại. Không sửa/deploy cloud trong pilot này.

### 4. Ghi nhận cấu hình và từng lần thử

Mở [phiếu pilot](quality-pilot-worksheet.md). Ghi package/revision/scope/exclusions, ứng dụng và deployment revision, CLI version, model/runtime nếu quan sát được, digest/version skill và prompt, cấu hình liên quan, run/attempt ID, thời điểm và người thao tác. Ghi `không quan sát được` khi thiếu trường; không lấy từ cấu hình hiện tại để điền thay cho lịch sử. Không chép `.env`, token, password, private key, raw credential URL hay nội dung credential vào bất kỳ biểu mẫu nào.

Giữ nguyên baseline, cả report, ledger, manifest, trạng thái, provenance/hash, và log admin đã lọc credential nếu policy cho phép. Không giả định mọi artifact đều tồn tại; ghi owner, tệp thiếu và lý do. Partial, timeout, failed, upload lỗi, không có ledger và validator không chạy đều là lần thử riêng, phải giữ trạng thái và đưa cạnh metric. Retry của ứng dụng xóa thư mục kết quả; runner cũng ghi đè một số log cố định. **Trước retry, sao snapshot artifacts của attempt cũ sang thư mục riêng, hoặc không retry nếu chưa lưu được.** Retry/repeat là attempt mới, không thay baseline.

Ghi vào bảng snapshot của worksheet: ID/path private duy nhất, owner, thời điểm đóng băng, danh sách tệp và SHA-256 từng tệp, trạng thái attempt, provenance, validator result và log được phép lưu. So sánh hash nguồn/bản sao và kiểm tra đủ inventory trước khi cho phép workspace cũ bị thay thế; nguồn thiếu gì phải giữ nguyên phân loại thiếu đó. Giữ snapshot đã xác nhận bất biến theo quyền/lưu trữ do admin quản lý; tạo bản copy mới riêng để validator ghi kết quả. Nếu chưa bảo toàn được snapshot, dùng job riêng cho attempt tiếp theo.

Nếu cần chạy validator, chỉ chạy trên bản copy riêng từ artifact đã duyệt, không chạy trên thư mục nguồn, production mount hay thư mục đang được worker ghi. Cách tạo copy an toàn ở bước 5.

### 5. Tạo snapshot copy an toàn

Chỉ dùng thư mục artifact tĩnh, đã duyệt và không chứa credential. Không snapshot toàn bộ `/data`, Codex home, volume runtime hay thư mục đang được ghi. Tạm dừng writer/capture attempt đã hoàn tất. Destination phải mới, riêng tư, rỗng; không dùng `cp -a`, symlink-following option hay symlink destination. Lệnh dưới đây dùng Bash cùng GNU `find`, `realpath`, `install`, `dirname`, `basename` và `cp` trên Linux. Thay đường dẫn bằng private path phù hợp; không đưa chúng vào báo cáo chia sẻ. Lệnh dừng nếu kiểm tra lỗi hoặc phát hiện symlink/file đặc biệt.

Nếu snapshot đầy đủ có `coverage_result.json` từ lần validation trước, giữ nguyên tệp đó trong snapshot. Admin chuẩn bị một thư mục nguồn validation riêng đã duyệt, chỉ gồm các ordinary files đầu vào validator/report/ledger/manifest, không gồm kết quả validator cũ; ghi inventory, hash đối chiếu và lý do loại tệp kết quả trong worksheet. Không xóa/sửa output baseline để vượt preflight. Dùng thư mục nguồn riêng này làm `PILOT_SOURCE` và vẫn yêu cầu destination mới cùng mọi kiểm tra bên dưới. Việc chuẩn bị đầu vào validation không thay thế snapshot đầy đủ của attempt.

```sh
set -euo pipefail
PILOT_SOURCE='/private/approved/attempt-01'
PILOT_COPY='/private/pilot-copies/package-01-attempt-01'
test -d "$PILOT_SOURCE"
test ! -L "$PILOT_SOURCE"
PILOT_SOURCE="$(realpath -e -- "$PILOT_SOURCE")"
PILOT_PARENT="$(realpath -e -- "$(dirname -- "$PILOT_COPY")")"
PILOT_COPY="$PILOT_PARENT/$(basename -- "$PILOT_COPY")"
case "$PILOT_COPY/" in "$PILOT_SOURCE/"*) exit 1 ;; esac
test ! -e "$PILOT_COPY"
test ! -L "$PILOT_COPY"
SOURCE_LINK="$(find -P "$PILOT_SOURCE" -type l -print -quit)"
test -z "$SOURCE_LINK"
SOURCE_SPECIAL="$(find -P "$PILOT_SOURCE" ! -type d ! -type f -print -quit)"
test -z "$SOURCE_SPECIAL"
install -d -m 700 -- "$PILOT_COPY"
cp -R --no-dereference -- "$PILOT_SOURCE"/. "$PILOT_COPY"/
COPY_LINK="$(find -P "$PILOT_COPY" -type l -print -quit)"
test -z "$COPY_LINK"
COPY_SPECIAL="$(find -P "$PILOT_COPY" ! -type d ! -type f -print -quit)"
test -z "$COPY_SPECIAL"
test ! -e "$PILOT_COPY/coverage_result.json"
test ! -L "$PILOT_COPY/coverage_result.json"
```

Các lệnh `find` được kiểm tra tự động và làm dừng đoạn lệnh nếu tìm thấy symlink hoặc file đặc biệt. Giữ mỗi `test` trên một lệnh riêng: Bash `set -e` không dừng khi lệnh bên trái `&&` thất bại. Xác nhận destination canonical và thư mục cha nằm trong vùng private do admin kiểm soát. Nếu có mount/containment không rõ, race với writer, quyền không riêng tư, output cũ, hoặc bất cứ điều gì không chắc: **dừng và coi validation là blocked**. Bản sao không làm thay đổi source; giữ hash/provenance và attempt ID. Tạo một destination mới cho mỗi validator invocation.

### 6. Chạy validator metadata trên bản copy

Trước tiên xác định path của script validator trong đúng revision đã duyệt, hash script và ghi `python3 --version`. So sánh digest với revision nguồn; nếu không xác định được thì ghi validator provenance không rõ và không dùng kết quả để so sánh. Đảm bảo `coverage_result.json` chưa tồn tại trong destination mới. Lệnh mẫu dưới đây chỉ cho gói copy đã qua preflight; không chạy trên production hoặc trong lúc pilot job đang hoạt động.

```sh
python3 /path/to/installed/boq-audit/scripts/validate_run.py \
  "/private/pilot-copies/package-01-attempt-01" \
  --out coverage_result.json
```

Mở đúng output mới bên trong thư mục copy, kiểm tra JSON object với `protocol_version: "v6.3"`, `gate: "METADATA_ONLY"`, `candidate_release_status` thuộc ba trạng thái dưới đây, `blocking_checks` là mảng chuỗi, `open_issue_count` là số nguyên không âm (không phải boolean), `automatic_publication_allowed: false`, `human_engineer_signoff_required: true` và `warning` là chuỗi. `report` chỉ nằm trong JSON tóm tắt stdout, không phải trường của file kết quả; stdout cũng chỉ ghi số lượng `blocking_checks`, không phải mảng. Ghi stdout/stderr đã lọc credential, exit code, validator digest và Python version.

Kết quả `BLOCKED` phải đi với exit 2 và có blocking checks. Exit 0 phải có mảng blocking checks rỗng: `PARTIAL_WITH_OPEN_ITEMS_REVIEW_REQUIRED` khi còn open issues; `READY_FOR_ENGINEER_REVIEW` khi không còn open issues. Mọi mâu thuẫn exit/status/count là `validation unavailable/blocked`. `READY_FOR_ENGINEER_REVIEW` chỉ là trạng thái metadata, cần người kiểm tra kỹ thuật ký riêng và **không** chứng minh tính đúng kỹ thuật hay cho phép phát hành. Nếu revision validator thay đổi schema, đối chiếu lại script đã pin trước khi sử dụng.

Bất kỳ exit code ngoài 0/2, thiếu Python/script, file không đọc được, timeout/crash/write failure, output không mới/thiếu/sai JSON/sai schema đều là `validation unavailable/blocked`. Giữ log admin đã redact và blocker; không dùng lại output cũ, không đoán từ `stdout`, không retry trên cùng folder. Tạo snapshot/copy mới rồi ghi attempt mới. Script validator hiện chỉ kiểm tra metadata, không độc lập tính hình học hay quantity.

**Các ca rehearsal validator — synthetic, không phải pilot accuracy:** malformed nested `issue_id` hoặc validator exit bất ngờ phải được ghi `unavailable/blocked`, không suy ra verdict; output cũ có sẵn phải bị preflight từ chối, không xóa/ghi đè để tái dùng; linked output, output mới thiếu/sai JSON hoặc lỗi ghi cũng phải blocked. Chỉ chạy các ca này trên bản synthetic riêng sau khi môi trường rehearsal được duyệt; lưu attempt/result riêng, không gộp với hồ sơ dự án. Kết quả rehearsal đã thực hiện được ghi riêng trong [bản ghi kiểm chứng](quality-pilot-verification.md); không coi đó là pilot accuracy hay bằng chứng cô lập worker.

### 7. Adjudicate, tính metric và quan sát kiến trúc sư

Với từng finding, adjudicator so sánh với nhãn tham chiếu: tối đa một finding cho một lỗi tham chiếu duy nhất, cùng đối tượng/loại lỗi và evidence tương thích. Gộp duplicate/split theo lỗi gốc. Ghi `được hỗ trợ`, `báo động sai`, `chưa giải quyết`, hoặc `ngoài phạm vi` kèm lý do. Chỉ supported và false alarm đã adjudicate vào precision; chỉ lỗi tham chiếu trong phạm vi đã adjudicate vào recall. Denominator 0 là `N/A`, không phải 100%. Hiển thị unresolved, ngoài phạm vi, exclusions, partial/failed và thiếu bằng chứng cạnh metric.

Adjudicate cả narrative/all-clear và các kiểm tra được hứa trong báo cáo kể cả khi có zero rows. Unsupported “đã kiểm tra hết”/“không có lỗi” hoặc omission của giới hạn làm readiness fail. Mỗi finding cần source filename/revision, PDF page và printed drawing ID hoặc BOQ sheet+cell/row, object/location, calculation/assumptions, cùng corrective/clarifying/justified-unresolved next step. Đánh dấu locator nguồn không tồn tại; không suy diễn locator.

Với người tham gia, dùng bản báo cáo tiếng Việt/Excel/PDF đang có và bài tập riêng chỉ chứa finding ID cùng nguồn trung tính. Không đưa answer key/reference sheet trước khi ghi phản hồi. Quan sát 5 phát hiện hoặc tất cả nếu ít hơn; chọn cả supported và ví dụ uncertainty/false-alarm nếu có, ghi thiếu loại mẫu khi không có. Một nhiệm vụ chỉ đạt khi người dùng tự tìm đúng nguồn và giải thích vấn đề, độ chắc chắn cùng hành động tiếp theo phù hợp kết luận adjudication, **không có trợ giúp admin**. Ghi phản hồi trước khi so đáp án; ghi thời gian, trợ giúp và kết quả từng nhiệm vụ. Yêu cầu tối thiểu một nhiệm vụ không rỗng; ghi rõ mọi nhiệm vụ đều pass hay không. Với mẫu nhỏ báo từng nhiệm vụ và mẫu số, không tuyên bố speed/usability chung.

### 8. Quyết định readiness có phạm vi

Chỉ admin quyết định `sửa và chạy lại`, `giới hạn theo phạm vi đánh giá`, `thu thập thêm bằng chứng`, hoặc `mở rộng đánh giá sau khi duyệt`. Để ghi `đạt trong đúng phạm vi đã đánh giá`, cần đồng thời:

- Đủ ba gói được duyệt và nhãn tham chiếu độc lập; attempt/baseline, provenance, exclusions và validator status được giải thích.
- Tất cả lỗi tham chiếu trọng yếu trong phạm vi được phát hiện với evidence cụ thể; không có critical claim thiếu hỗ trợ; unresolved và ambiguity được công khai. Nếu mẫu số critical bằng 0, ghi `chưa đánh giá` cho coverage critical, không suy ra pass; liệt kê lớp lỗi/mức độ đã và chưa có đại diện.
- Không còn gói partial, failed, hoặc bị chặn cấu trúc được tính như pass.
- Nội dung narrative không overclaim, không bỏ giới hạn; mọi phát hiện được adjudicate.
- Có nhiệm vụ quan sát không rỗng và tất cả nhiệm vụ đã quan sát đạt tiêu chí đặt trước.

Phán quyết chỉ áp dụng cho corpus, revision và phạm vi ghi trên worksheet. Readiness không roll out tự động, không xác nhận tổng quát, không thay thế quyết định kỹ sư/kiến trúc sư chịu trách nhiệm.

## Reference: rehearsal trải nghiệm và tài liệu

**Rehearsal ba bước, mục tiêu tối đa 5 phút:** người mới bắt đầu tại README; (1) tìm giới hạn của trạng thái `COMPLETED`, (2) mở phiếu và tự giải thích source match của ví dụ supported, (3) tự giải thích ví dụ unresolved cùng giới hạn kết luận; kiểm tra thêm trường hợp zero finding vẫn là chưa đánh giá. Ghi thời gian, hoàn tất, trợ giúp và chỗ gây nhầm vào worksheet. Nếu tài liệu không đủ rõ, sửa docs và lặp lại rehearsal; không ghi như đã đạt nếu chưa quan sát. Đây là thời gian đọc hướng dẫn, không gộp với cài đặt/model/cloud audit.

**Kiểm tra trước khi chia sẻ báo cáo** (ghi pass/fail/block, không mặc định đã làm): trang đầu hiển thị package/revision/scope và lời nhắc human review; execution/evidence/pilot status riêng; limitation/coverage cạnh kết quả. Với giao diện/tệp có thể xem, kiểm tra viewport 375/768/1440 px, zoom 200%, bàn phím và screen reader, thứ tự heading, link và bảng tràn; chữ tương đương ít nhất 16 px, contrast chữ thường tối thiểu 4.5:1, control quan trọng có vùng bấm ít nhất 44 px và focus nhìn thấy được. Trạng thái phải hiểu được không dựa riêng vào màu. Ghi từng viewport/phương thức/check cùng bằng chứng, blocker và owner trong worksheet. Không sửa UI như một phần của runbook.

## Troubleshooting

| Vấn đề | Hành động admin | Thông báo ngắn cho người dùng | Owner |
|---|---|---|---|
| Chat/session bị thay thế hoặc kết quả đến từ session khác | Đối chiếu job/attempt ID và file nguồn; giữ cả attempt, không ghép chéo. | “Lượt xử lý bị gián đoạn. Báo cáo này chưa được đánh giá.” | Admin |
| Concurrent/pending hoặc worker capacity | Ghi thời điểm và trạng thái queue; không submit lặp như cùng attempt. Điều phối theo cấu hình được duyệt. | “Hồ sơ đang chờ xử lý. Chưa có kết quả đánh giá.” | Admin |
| Hết quota/capacity/model unavailable | Lưu error đã redact; giữ failed attempt; chờ admin kiểm tra quota/cấu hình rồi tạo attempt mới. | “Chưa thể hoàn tất lượt xử lý; vui lòng báo admin.” | Admin |
| Dự án/revision/file không đúng hoặc stale | Dừng đánh giá; so sánh package ID, hash, revision và artifact manifest; yêu cầu admin xác nhận nguồn đúng. | “Nguồn chưa khớp hồ sơ được duyệt.” | Admin/người gửi |
| Gateway/timeout/interrupted audit | Không coi partial report là kết quả đầy đủ; chụp artifact/log trước retry; ghi attempt riêng. | “Lượt kiểm tra bị gián đoạn; báo cáo có thể chưa đầy đủ.” | Admin |
| Thiếu CLI/skill/evidence hoặc provenance | Không tự cài/đổi cấu hình trong attempt hiện tại; ghi trường thiếu, khôi phục từ revision đã duyệt cho lần mới. | “Chưa đủ thành phần hoặc bằng chứng để đánh giá.” | Admin |
| Validator BLOCKED, lỗi, hoặc stale output | Coi blocked; không reuse JSON cũ; giữ redacted diagnostics, fresh safe copy và attempt mới sau khi xử lý. | “Kiểm tra metadata bị chặn; báo cáo chưa được đánh giá.” | Admin |

Không đưa admin path, raw logs, stack trace, credential, hoặc lý do chứa dữ liệu nội bộ vào thông báo người dùng.

## Decision log

Mỗi thay đổi pilot ghi ngày, package/revision/scope, lý do, người phê duyệt và link tới worksheet/snapshot private. Dùng dữ liệu tổng hợp chỉ để rehearsal; không sửa nhãn, threshold, prompt hay skill sau khi xem holdout rồi tiếp tục gọi đó là holdout. Nếu điều chỉnh, tăng revision và đánh giá trên bộ giữ lại mới.
