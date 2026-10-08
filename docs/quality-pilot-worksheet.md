# Phiếu pilot chất lượng BOQ

**Chỉ dành cho admin và người thẩm tra.** Không gửi cả phiếu này cho kiến trúc sư tham gia quan sát: họ chỉ nhận báo cáo thông thường và phần bài tập không có đáp án. Tài liệu này là mẫu ghi nhận; trạng thái chưa điền được hiểu là **chưa đánh giá**, không phải đạt.

> **SYNTHETIC · NOT PILOT DATA.** Ví dụ có chữ `SYNTHETIC` bên dưới hoàn toàn hư cấu, chỉ để giải thích cách ghi. Không phải hồ sơ dự án, không phải kết quả pilot, không được tính vào số liệu pilot.

## 1. Thông tin gói và phạm vi

| Trường | Ghi nhận |
|---|---|
| Mã gói nội bộ | |
| Phiên bản BOQ / ngày nhận | |
| Tên và revision từng bản vẽ, bảng tính, tài liệu nguồn | |
| Bộ môn / hạng mục / giai đoạn được đánh giá | |
| Hạng mục và tài liệu loại trừ, lý do | |
| Người kiểm tra độc lập; năng lực/phạm vi chuyên môn | |
| Người vận hành; người adjudicate bất đồng | |
| Dung sai, quy ước đo và cơ sở phê duyệt theo từng loại kiểm tra | |
| Môi trường chạy được duyệt; bằng chứng ranh giới truy cập theo từng job | |
| Revision ứng dụng/triển khai; CLI version; model/runtime nếu quan sát được; digest skill/prompt; cấu hình liên quan | |
| ID lần chạy; thời gian bắt đầu/kết thúc; múi giờ | |

Không ghi token, password, private key, nội dung `.env` hay URL có credential. Ghi `không quan sát được` khi không lấy được provenance; không đoán hoặc điền lại sau từ một lần chạy khác.

### Trạng thái phải ghi riêng

| Loại trạng thái | Giá trị / quy tắc |
|---|---|
| **Thực thi** | `Chưa chạy`; `Đang chạy`; `Chạy hoàn tất — chưa xác nhận chất lượng`; `Một phần`; `Thất bại`; `Bị chặn kiểm tra cấu trúc`. Hoàn tất tiến trình không đồng nghĩa báo cáo đúng. |
| **Bằng chứng** | `Đầy đủ`; `Thiếu`; `Chạy offline — thiếu provenance`; `Chưa đánh giá`. Ghi rõ tệp/ledger/log nào thiếu hoặc không thể xác minh. |
| **Đánh giá pilot** | `Đang chờ đầu vào`; `Chưa đánh giá`; `N/A — không có mẫu số`; `Chưa đủ bằng chứng`; `Không đạt`; `Đạt trong đúng phạm vi ghi dưới đây — admin duyệt`. Không tự mở rộng phạm vi. |

## 2. Danh sách gói và lần chạy

| Gói / revision | Phạm vi và loại mẫu (lỗi đã kiểm chứng / sạch đã kiểm / mơ hồ) | Lần chạy và bản triển khai | Thực thi | Bằng chứng / exclusions | Đánh giá |
|---|---|---|---|---|---|
| | | | | | |
| | | | | | |
| | | | | | |

Ba gói đại diện phải được duyệt và có nhãn tham chiếu độc lập trước khi kết luận phạm vi pilot. Gói đầu dùng để diễn tập quy trình; riêng nó không chứng minh sẵn sàng. Dữ liệu tổng hợp được gắn nhãn riêng, không gộp với gói dự án.

Mỗi lần chạy, kể cả timeout/thất bại/chạy lại, là một dòng riêng. Trước khi nhấn retry, lưu snapshot riêng của **lần chạy cũ**; retry có thể xóa thư mục kết quả và ghi đè log. Không thay baseline bằng lần chạy mới.

### Inventory snapshot và validator (admin giữ riêng)

| Gói/attempt | Snapshot ID / path private / owner / thời điểm đóng băng | Tệp nguồn + SHA-256 / tệp copy + SHA-256 đã khớp | Trạng thái attempt / provenance / tệp thiếu và lý do | Validator: path + SHA-256 / Python version / copy ID | Exit / candidate_release_status / fresh JSON hợp schema / diagnostics đã redact | Snapshot đủ, bất biến trước retry? |
|---|---|---|---|---|---|---|
| | | | | | | |

Giữ reports, mọi ledger, manifest, status và log được phép trong inventory; không sửa snapshot baseline để chạy validator. Mỗi invocation dùng copy/output mới; thiếu hoặc stale result là `unavailable/blocked`. Nếu không lưu được snapshot, dùng job riêng, không ghi đè attempt cũ.

## 3. Nhãn tham chiếu độc lập

| Ref ID | BOQ: tệp/revision/sheet/cell hoặc hàng | Đối tượng/vị trí | Bản vẽ: tệp/revision/trang PDF/số hiệu bản vẽ | Lỗi và mức độ | Tính toán/cơ sở hình học, đơn vị, giả định và dung sai đã duyệt | Người kiểm tra | Trạng thái |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

Chỉ đưa lỗi vào mẫu số khi người có chuyên môn đã kiểm tra độc lập và kết luận nằm trong phạm vi. Ghi riêng `chưa giải quyết`, `ngoài phạm vi`, `chưa kiểm`; không lặng lẽ tính chúng là đúng hoặc sai. Lưu revision/hash nguồn cùng snapshot riêng, không sửa nhãn gốc khi có phát hiện mới.

### Ví dụ đã giải để hướng dẫn người bảo trì

**SYNTHETIC · NOT PILOT DATA.** Giả sử tài liệu hư cấu `SYNTHETIC-DRAWING-R01.pdf`, PDF trang 2, số bản vẽ in `S-02`, thể hiện đối tượng `D-17` với kích thước giả định 2.00 × 1.00 × 2.00 m; BOQ hư cấu ghi 5.00 m³. Chỉ trong ví dụ này, người kiểm tra độc lập xác nhận các kích thước/đơn vị/phạm vi, tính 4.00 m³, và phê duyệt cách so sánh; chênh 1.00 m³ là lỗi tham chiếu. Nếu báo cáo nêu chính hàng D-17, trích đúng nguồn và tính chênh có thể kiểm lại, adjudicate là một phát hiện được hỗ trợ. Đây không phải quy tắc đo hay ngưỡng sai số áp dụng cho dự án thật.

**SYNTHETIC · NOT PILOT DATA.** Nếu một báo cáo khác kết luận định lượng cho đối tượng trong PDF trang 3 nhưng trang đó thiếu kích thước hoặc tỷ lệ cần thiết, không suy diễn là lỗi hay đúng. Ghi `chưa giải quyết`, nêu bằng chứng thiếu và việc cần người có trách nhiệm cung cấp/xác nhận. Không thêm vào mẫu số precision/recall.

## 4. Đối chiếu từng phát hiện

| Finding ID / tệp báo cáo | Vị trí được báo và trích dẫn chính xác | Cùng lỗi tham chiếu? Ref ID | Kết quả adjudication | Lý do / bằng chứng / bước tiếp theo |
|---|---|---|---|---|
| | | | `được hỗ trợ` / `báo động sai` / `chưa giải quyết` / `ngoài phạm vi` | |

Ghép tối đa một-một giữa phát hiện và lỗi tham chiếu: cùng đối tượng thực, cùng loại lỗi, bằng chứng tương thích. Nhiều cảnh báo cho một lỗi, hoặc một cảnh báo chia thành nhiều phần, phải adjudicate theo lỗi gốc để không đếm trùng. Người adjudicate quyết định trường hợp mơ hồ trước khi tính số.

Một trích dẫn hữu ích nêu tên tệp và revision, PDF page cùng số hiệu in trên bản vẽ; với BOQ, sheet và cell/hàng; thêm đối tượng/vị trí và phép tính/giả định cần kiểm tra. Nếu nguồn không có locator đó, ghi rõ `không có trên nguồn`; không tự bịa trang, sheet hay số bản vẽ. Nhận xét chung như “kiểm tra lại khối lượng” không phải phát hiện được hỗ trợ.

## 5. Số liệu trong phạm vi đã adjudicate

Với cùng phạm vi và một lần chạy:

- `precision = phát hiện lỗi duy nhất được hỗ trợ / (phát hiện lỗi duy nhất được hỗ trợ + báo động sai duy nhất)`.
- `recall = lỗi tham chiếu trong phạm vi được ghép / tổng lỗi tham chiếu đã adjudicate trong phạm vi`.
- Mẫu số bằng 0 thì ghi `N/A — không có mẫu số`; tuyệt đối không ghi 100%.

| Gói/lần chạy/phạm vi | Ref errors đã adjudicate | Supported unique | False-alarm unique | Unresolved | Ngoài phạm vi | Missed ref errors | Precision | Recall | Partial/failed/thiếu bằng chứng |
|---|---:|---:|---:|---:|---:|---:|---|---|---|
| | | | | | | | | | |

Lặp lại bảng số liệu theo **từng lớp lỗi và mức độ** trong mỗi gói/attempt; không chỉ ghi tổng gói. Ghi riêng thời gian thao tác admin/người kiểm tra và bước tiếp theo; đây là mô tả effort, không phải bằng chứng tiết kiệm thời gian tổng quát.

| Bộ môn / lớp lỗi / mức độ / required check | Có đại diện trong corpus? | Ref count / found / missed / chưa kiểm hoặc bất đồng bị loại | Phạm vi, giới hạn bắt buộc phải nêu trước chạy | Bằng chứng narrative sau chạy / đạt hay thiếu |
|---|---|---|---|---|
| | | | | |

Đóng băng required checks/limitations trước khi chạy, giữ các bất đồng ngoài mẫu số với lý do. Với phát hiện mới được xác nhận độc lập, lưu reference revision mới cùng so sánh baseline gốc; không âm thầm cải thiện điểm baseline.

**Ví dụ công thức — SYNTHETIC · NOT PILOT DATA:** một lỗi tham chiếu đã adjudicate, một phát hiện được hỗ trợ, một báo động sai, không bỏ sót: precision = 1/(1+1) = 50%; recall = 1/1 = 100%. Các số minh họa không phải kết quả của ứng dụng.

Không cộng hồ sơ partial, failed, bị chặn cấu trúc hoặc thiếu mẫu số vào một con số trung bình tạo ấn tượng đạt. Hiển thị trạng thái và giới hạn bên cạnh metric. Đối chiếu cả nội dung tường thuật: báo cáo không có dòng phát hiện vẫn phải được adjudicate; “không phát hiện” không chứng minh “đã kiểm tra hết”.

## 6. Nội dung báo cáo và phạm vi phủ

Đánh dấu `Có / Không / Chưa đánh giá / N/A`, kèm vị trí báo cáo và bằng chứng.

| Câu hỏi | Trạng thái | Bằng chứng / người xử lý / bước tiếp theo |
|---|---|---|
| Trang đầu phân biệt rõ gói, revision, phạm vi, exclusions và lời nhắc người có chuyên môn phải review? | | |
| Thực thi, bằng chứng, và đánh giá pilot có nhãn riêng? | | |
| Hạn chế và phần chưa đánh giá được nêu cạnh kết quả? | | |
| Mỗi phát hiện có locator nguồn chính xác, phép tính/giả định và hành động khắc phục/làm rõ/biện minh còn thiếu? | | |
| Nguồn thiếu locator được đánh dấu rõ, không bịa định danh? | | |
| Báo cáo không tuyên bố all-clear hoặc “đã xác minh đầy đủ” nếu không có bằng chứng cho việc đó? | | |
| Các cảnh báo trùng/lỗi gốc và bất đồng được adjudicate? | | |
| Failed/partial/bị chặn và lần thử bị thiếu đều được báo cáo? | | |
| Người có chuyên môn đã xác nhận từng vấn đề trọng yếu theo quy trình nội bộ? | | |

Mẫu số lỗi trọng yếu bằng 0 được báo `Chưa đánh giá`, không phải “0% lỗi” hoặc “đạt”. Nếu số dòng phát hiện bằng 0, vẫn phải điền phần tường thuật, phạm vi và giới hạn.

### Các trạng thái minh họa cần giữ nguyên ý nghĩa

| Tình huống | Cách ghi |
|---|---|
| Chưa nhận đủ nguồn hoặc nhãn | `Đang chờ đầu vào — chưa chạy` |
| Chạy xong, không có finding rows | `Chạy hoàn tất — chưa xác nhận chất lượng`; `phủ lỗi: chưa đánh giá` nếu mẫu số chưa có |
| Không có lỗi tham chiếu đã adjudicate | `Recall: N/A — không có mẫu số`; không coi là đạt |
| Chỉ có phát hiện chưa adjudicate | `Đánh giá pilot: chưa đủ bằng chứng`; báo unresolved riêng |
| Chạy offline, thiếu revision/model/skill/prompt provenance | `Chạy offline — thiếu provenance`; không so sánh như cùng cấu hình |
| Một phần, thất bại, timeout hoặc kiểm tra cấu trúc bị chặn | Giữ nguyên lần thử; `không đủ điều kiện kết luận readiness` |
| Không có lỗi trọng yếu trong mẫu số | `Lỗi trọng yếu: chưa đánh giá` |
| Có phát hiện trong phạm vi, được hỗ trợ | Chỉ gọi được hỗ trợ sau adjudication; ghi metric và phạm vi cụ thể |

## 7. Quan sát người dùng (admin giữ riêng)

Chọn một báo cáo đã duyệt. Người tham gia nhận **báo cáo thông thường**, không nhận phiếu adjudication/đáp án. Quan sát viên đưa ID và nguồn trung tính cho 5 phát hiện (hoặc tất cả nếu ít hơn), gồm supported và ví dụ uncertainty/false-alarm nếu có; ghi loại mẫu thiếu. Không tiết lộ đáp án trước khi ghi nhận phản hồi.

| Người dùng ẩn danh / vai trò | Phát hiện ID | Tự tìm được vị trí? | Diễn giải và mức độ chắc chắn | Hành động tiếp theo họ chọn | Thời gian | Trợ giúp admin / blocker | So với đáp án sau phản hồi / đạt hay không / lý do |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

Tiêu chí đặt trước: tự tìm đúng nguồn, giải thích vấn đề, uncertainty và hành động tiếp theo phù hợp kết luận adjudication, không được admin giúp. Có trợ giúp thì nhiệm vụ không đạt độc lập; giữ nguyên kết quả, sửa vấn đề cụ thể rồi quan sát lại với record mới.

Ghi tổng nhiệm vụ quan sát / hoàn tất / đạt; mọi nhiệm vụ và tiêu chí pass/fail đã duyệt trước. Mẫu nhỏ phải báo mẫu số và kết quả từng nhiệm vụ; không tuyên bố tốc độ hay usability đại diện. Pilot readiness cần nhiệm vụ không rỗng và tất cả nhiệm vụ được quan sát đều đạt.

## 8. Quyết định có giới hạn

| Phạm vi chính xác đã đánh giá | Tất cả critical refs trong phạm vi được phát hiện với bằng chứng? | Critical claim không được hỗ trợ? | Lần chạy thiếu/failed/partial được giải trình? | Nội dung narrative đạt? | Quan sát đạt? | Quyết định và người duyệt |
|---|---|---|---|---|---|---|
| | | | | | | |

Chỉ admin ghi quyết định: `sửa và chạy lại`; `giới hạn theo phạm vi này`; `tiếp tục thu thập bằng chứng`; hoặc `mở rộng đánh giá sau khi duyệt`. Chỉ ghi “đạt trong phạm vi đã đánh giá” khi tiêu chí trong runbook đều có bằng chứng; không tự động mở rollout hay khẳng định tính chính xác chung.

## 9. Kiểm chứng tài liệu, giao diện và effort

| Lần rehearsal / người mới ẩn danh | Bắt đầu README / kết thúc / thời gian | Tự giải thích supported source match, unresolved và giới hạn? | Zero finding vẫn chưa đánh giá? | Trợ giúp / nhầm lẫn / đạt mục tiêu 5 phút? | Việc sửa / owner / lần quan sát lại |
|---|---|---|---|---|---|
| | | | | | |

| Giao diện/tệp / revision / trạng thái đang xem | Viewport / zoom / bàn phím hoặc screen reader | Check theo runbook | Pass / fail / blocked + bằng chứng | Owner / bước tiếp theo |
|---|---|---|---|---|
| | | | | |

| Gói/attempt | Người / vai trò | Phút chuẩn bị nhãn | Phút adjudication | Phút trợ giúp admin | Blocker / hành động tiếp theo |
|---|---|---|---|---|---|
| | | | | | |

Để trống nghĩa là chưa quan sát; build/typecheck không thay thế rendered accessibility hay rehearsal của người mới.
