# Khung báo cáo thiết kế: Doc2PDF (Final Project 08)

> Đề yêu cầu một **báo cáo thiết kế ngắn**. Mục lục dưới đây bám theo danh sách nộp bài và các tiêu chí chấm điểm của đề. Mỗi quyết định cần có **lý do viết ngắn gọn** (phần Kiến trúc và lý giải chiếm 25% điểm).
> Trạng thái: ⬜ chưa làm · 🟨 đang làm · ✅ xong

## Trang bìa và thông tin nộp bài

| Mục | Nội dung | Phụ trách | Trạng thái |
|---|---|---|---|
| Thông tin nhóm | Tên môn, đề tài, 5 thành viên, vai trò | Muội | ⬜ |
| URL ứng dụng | Địa chỉ web đang chạy | Như | ⬜ |
| Tài khoản demo | Một user, một operator (không ghi mật khẩu thật vào repo công khai) | Khôi | ⬜ |
| Liên kết | Repo, CI/CD pipeline, bảng công việc | Muội | ⬜ |

## 1. Tổng quan và phạm vi (≈ 0,5 trang)

- Bài toán: dịch vụ chuyển đổi tài liệu sang PDF, xử lý bất đồng bộ trong container có giới hạn
- Hai vai trò: người dùng và operator
- Phạm vi: TXT, Markdown, PNG/JPG, tối đa 10 MB
- Những gì **không** làm (ví dụ giới hạn đồng thời theo user, hủy job) và vì sao

Phụ trách: Muội · ⬜

## 2. Kiến trúc và lý giải lựa chọn (25% điểm)

- 2.1 Sơ đồ kiến trúc **thực tế**: tên dịch vụ, đường mạng, ranh giới tin cậy, miền lỗi *(thay bản logic ở `docs/architecture.md`)*
- 2.2 Lý do chọn nền tảng cloud, vùng (region), gói dịch vụ
- 2.3 Lý do chọn từng thành phần (API, database, storage, queue, worker)
- 2.4 Định danh, mạng và bảo mật: OIDC, workload identity, nơi lưu secret, cách kết nối database riêng tư
- 2.5 Mô hình dữ liệu: `ConversionJob`, `SourceObject`, `ResultObject`, các trường `attempts`, `lease_expires_at`, `source_checksum`, `converter_version`
- 2.6 Đánh đổi đã cân nhắc (vì sao không chọn phương án khác)

Phụ trách: Muội (khung, tổng hợp) · Như (mạng, IaC) · Khôi (API, data model) · Lộc (worker) · ⬜

## 3. Quy trình xử lý và phục hồi lỗi (15%)

- 3.1 Luồng chính: nhận job, đưa vào queue, chạy container có giới hạn, xuất PDF hoặc báo lỗi
- 3.2 Lease, timeout, số lần thử, dead-letter
- 3.3 Outbox, relay, dedup key, xử lý message giao lại
- 3.4 Điều kiện thành công: PDF đọc được
- 3.5 Giới hạn tài nguyên: CPU, RAM, thời gian, bộ nhớ tạm

Phụ trách: Lộc · Khôi · ⬜

## 4. Bảo mật và tính đúng đắn (15%)

- Kiểm tra định dạng, size, magic bytes
- Quyền truy cập: user với operator, kiểm tra ở API
- Storage private, signed URL có hạn, HTTPS
- Xử lý secret, không ghi log thông tin nhạy cảm
- Kết quả **test gọi thẳng API** (S1 đến S11 trong `docs/test-plan.md`)

Phụ trách: Khôi · Muội (kết quả test) · ⬜

## 5. Triển khai và tự động hóa (20%)

- Hạ tầng bằng code (Terraform): cách tạo lại từ đầu
- Pipeline CI/CD: các bước, lần triển khai thành công
- **Demo rollback**
- Môi trường cục bộ bằng Docker Compose và cách chuyển lên cloud (adapter)

Phụ trách: Như · ⬜

## 6. Độ tin cậy và quan sát (15%)

- Metric: queue age, thời lượng chuyển đổi, số timeout, đỉnh bộ nhớ, chi phí mỗi lần chuyển đổi thành công
- Dashboard (ảnh chụp)
- **Một alert** gắn với bài tập lỗi: điều kiện, cửa sổ đánh giá, độ trễ dự kiến, hành động khắc phục đầu tiên
- Job ID xuyên suốt log, API, worker

Phụ trách: Muội · Như · ⬜

## 7. Kiểm thử và bằng chứng

| Test | Nội dung | Kết quả | Bằng chứng |
|---|---|---|---|
| Test 1 | Job ID trong 2 giây | | |
| Test 2 | 20 file hợp lệ + file lỗi | | |
| Test 3 | Kill worker, lease hết hạn | | |
| Bài tập lỗi | Chuyển đổi chậm, timeout, dọn dữ liệu tạm | | |
| Load test | Thời lượng, mức đồng thời, warmup, dataset, tỉ lệ lỗi, p95 | | |

Lưu timestamp, dữ liệu đầu vào, kết quả thô, log trong `docs/evidence/`.

Phụ trách: Muội · cả nhóm · ⬜

## 8. Ứng dụng và dữ liệu (15%)

- Giao diện user: upload, theo dõi tiến độ, retry, tải PDF có hạn
- Giao diện operator: danh sách job, job lỗi hoặc kẹt, thử lại
- Seed script và bộ dữ liệu tham chiếu (`data/`, `expected.csv`)

Phụ trách: Nhàn · Muội (dữ liệu) · ⬜

## 9. Ước tính chi phí hàng tháng (một phần của 10%)

- Nêu rõ: region, gói dịch vụ, số giờ chạy, dung lượng lưu trữ, số request, backup, egress
- Dựa trên **khối lượng công việc** giả định (số job mỗi ngày, kích thước trung bình)
- Chi phí mỗi lần chuyển đổi thành công
- Lưu ý: cảnh báo ngân sách không phải giới hạn chi tiêu cứng

Phụ trách: Muội · Như · ⬜

## 10. Hướng dẫn dọn dẹp

- Danh sách tài nguyên cần xóa (compute chạy không, database, load balancer, dữ liệu lưu giữ)
- Lệnh hoặc bước thực hiện (ví dụ `terraform destroy`)
- Cách kiểm tra không còn tài nguyên tính phí
- Chỉ dọn sau khi đã chụp đủ bằng chứng và giảng viên chấm xong

Phụ trách: Như · ⬜

## 11. Kết luận và bài học (≈ 0,5 trang)

- Điều làm tốt, điều chưa làm được, nếu có thêm thời gian
- Phân công và đóng góp của từng thành viên

Phụ trách: Muội · ⬜

## Phụ lục: kịch bản demo (10 đến 15 phút)

| Phút | Nội dung | Người trình bày |
|---|---|---|
| 0–2 | Giới thiệu, sơ đồ kiến trúc thực tế | |
| 2–6 | Hành trình người dùng bình thường (đăng nhập, upload, xem tiến độ, tải PDF) | |
| 6–9 | Test đúng đắn chính (Test 2: file hợp lệ và file lỗi) | |
| 9–13 | Lỗi và phục hồi (chuyển đổi chậm, timeout, kill worker, alert) | |
| 13–15 | Giám sát, chi phí, rollback, kết luận | |

## Danh sách nộp bài (đối chiếu cuối cùng)

- [ ] URL ứng dụng và tài khoản demo
- [ ] Mã nguồn và mã hạ tầng
- [ ] Seed data và test
- [ ] Pipeline CI/CD
- [ ] Sơ đồ kiến trúc thực tế
- [ ] Báo cáo thiết kế ngắn
- [ ] Bằng chứng giám sát và lỗi
- [ ] Ước tính chi phí hàng tháng theo khối lượng
- [ ] Hướng dẫn dọn dẹp
- [ ] Bài thuyết trình demo 10–15 phút
