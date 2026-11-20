# Log có job ID, metric, dashboard và alert (bản nháp)

> Bám yêu cầu của đề: theo dõi queue age, thời lượng chuyển đổi, timeout, đỉnh bộ nhớ, chi phí mỗi lần chuyển đổi thành công; **một dashboard và một alert** gắn với bài tập lỗi; job ID xuyên suốt; **không log** thông tin xác thực, signed URL, nội dung file riêng tư.
> Các số ngưỡng ghi **(đề xuất)** cần Lộc và Như xác nhận.

## 1. Định dạng log (JSON, mỗi dòng một sự kiện)

```json
{"ts": "2026-11-20T10:30:15.123+07:00", "level": "INFO", "service": "worker",
 "event": "job_started", "job_id": "9f3c...", "request_id": "b71a...",
 "attempt": 2, "format": "txt", "size_bytes": 20480}
```

| Trường | Bắt buộc | Ghi chú |
|---|---|---|
| `ts` | Có | ISO 8601 có múi giờ |
| `level` | Có | DEBUG, INFO, WARN, ERROR |
| `service` | Có | `api`, `relay`, `worker`, `frontend` |
| `event` | Có | Tên sự kiện cố định (bảng dưới) |
| `job_id` | Có với mọi sự kiện của job | Cùng một giá trị ở API, relay, worker |
| `request_id` | API trả về trong header, worker ghi lại | Nối phản hồi API với log |
| `attempt` | Với worker | Lần thử thứ mấy |
| `duration_ms` | Với sự kiện kết thúc | Thời lượng |
| `error_type` | Khi lỗi | Dùng mã ở mục 6 của `contract.md` |

**Sự kiện cố định (đề xuất):** `job_created`, `upload_rejected`, `outbox_published`, `message_received`, `message_duplicate_skipped`, `job_started`, `lease_expired`, `conversion_timeout`, `job_succeeded`, `job_failed`, `message_dead_lettered`, `job_retried_by_operator`.

**Tuyệt đối không ghi:** token, mật khẩu, connection string, signed URL, nội dung file, tên file nếu chứa dữ liệu cá nhân. Chỉ ghi `job_id`, kích thước, định dạng.

## 2. Metric

| Metric | Cách tính | Nguồn | Dùng để |
|---|---|---|---|
| Tuổi hàng đợi (queue age) | Tuổi message cũ nhất đang chờ | Service Bus | Phát hiện worker không xử lý |
| Độ dài hàng đợi | Số message đang chờ | Service Bus | Tải |
| Thời lượng chuyển đổi | `duration_ms` của `job_succeeded` và `job_failed`, p50 và p95 | Log worker | Hiệu năng |
| Số timeout | Đếm `conversion_timeout` | Log worker | **Gắn với bài tập lỗi** |
| Số job `failed`, dead-letter | Đếm `job_failed`, `message_dead_lettered` | Log, Service Bus | Chất lượng |
| Job `running` quá lease | Đếm job `running` mà `lease_expires_at < now` | Database | Job kẹt |
| Đỉnh bộ nhớ worker | Memory working set lớn nhất mỗi lần chạy | Container Apps metrics | Kiểm tra giới hạn tài nguyên |
| Chi phí mỗi lần chuyển đổi thành công | Chi phí kỳ / số job `succeeded` | Cost Management + đếm job | Chi phí (xem `cost-estimate.md`) |

## 3. Dashboard (một trang, 6 ô)

1. Tuổi hàng đợi và độ dài hàng đợi theo thời gian
2. Số job theo trạng thái (queued, running, succeeded, failed)
3. Thời lượng chuyển đổi p50 và p95
4. Số timeout theo giờ
5. Đỉnh bộ nhớ worker
6. Chi phí lũy kế trong tháng so với credit

Triển khai: Azure Monitor Workbook hoặc dashboard trong Log Analytics (Như chọn). Lưu ảnh chụp vào `docs/evidence/`.

## 4. Alert gắn với bài tập lỗi (chuyển đổi chậm, timeout)

| Mục | Giá trị (đề xuất) |
|---|---|
| Điều kiện | Số `conversion_timeout` ≥ 1 **hoặc** tuổi hàng đợi > 120 giây |
| Cửa sổ đánh giá | 5 phút |
| Tần suất kiểm tra | 1 phút |
| Độ trễ dự kiến từ lúc lỗi đến lúc báo | Timeout 30 giây + tần suất kiểm tra + trễ nạp log (thường vài phút): **ghi số đo được thật** |
| Gửi đến | Email của nhóm trưởng và Như |
| **Hành động khắc phục đầu tiên** | Mở dashboard, lọc log theo `job_id` của job timeout, xem đó là file chậm hay worker quá tải; nếu job còn `running` quá lease thì kiểm tra reaper đã chuyển lại `queued` chưa; nếu hết lượt thử thì xem dead-letter và retry |

Sau khi chạy bài tập lỗi chuyển đổi chậm, ghi vào bảng: thời điểm gây lỗi, thời điểm alert đến, độ trễ thật.

## 5. Việc cần làm

- [ ] Khôi và Lộc: ghi log theo mục 1 (đặc biệt `job_id`)
- [ ] Như: bật Log Analytics cho Container Apps, tạo dashboard và alert
- [ ] Muội: kiểm tra log không chứa dữ liệu nhạy cảm (tìm chuỗi `token`, `sig=`, `Bearer` trong log đã thu)
