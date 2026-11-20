# Khung ước tính chi phí hàng tháng trên Azure

> **Đây là khung, chưa có số tiền.** Giá đơn vị của Azure thay đổi theo vùng và gói, nên **phải điền từ Azure Pricing Calculator** (azure.microsoft.com, trang Pricing calculator) đúng vùng và gói nhóm dùng. Không điền giá từ trí nhớ.
> Đề yêu cầu: ước tính theo **khối lượng công việc**, nêu vùng, gói, số giờ chạy, dung lượng, số request, backup, egress; và nhắc rằng cảnh báo ngân sách **không phải** giới hạn chi tiêu cứng.

## 1. Thông tin chung

| Mục | Giá trị |
|---|---|
| Nền tảng | Azure for Students (credit 100 USD, theo trang chính thức của Microsoft; kiểm tra lại số dư thật trong portal) |
| Vùng (region) | ___ (Như ghi sau khi chốt) |
| Ngày lập ước tính | ___ |
| Người lập | Muội, Như |

## 2. Giả định khối lượng công việc (đề xuất, sửa cho hợp lý)

| Tham số | Ký hiệu | Giá trị giả định |
|---|---|---|
| Số job mỗi tháng | J | 3.000 |
| Tỉ lệ job thành công | s | 90% |
| Kích thước file nguồn trung bình | S | 2 MB |
| Kích thước PDF trung bình | P | 1 MB |
| Thời gian chuyển đổi trung bình mỗi job | t | 5 giây |
| Số lần thử trung bình mỗi job | a | 1,1 |
| Số lượt tải PDF mỗi job thành công | d | 1,5 |
| Thời gian lưu file (expiry) | E | 7 ngày |
| Số request API mỗi job (upload, trạng thái, tải) | r | 15 |

Số liệu trên là **giả định để minh hoạ cách tính**; thay bằng số đo được ở load test khi có.

## 3. Bảng chi phí theo dịch vụ

| Dịch vụ | Đại lượng tính tiền | Cách tính | Giá đơn vị | Chi phí/tháng |
|---|---|---|---|---|
| Container Apps: API, frontend, relay | vCPU-giây, GiB-giây, số request; lưu ý replica tối thiểu | (giờ chạy × cấu hình) − phần miễn phí | ___ | ___ |
| Container Apps Jobs: chuyển đổi | vCPU-giây, GiB-giây | J × a × t × cấu hình job | ___ | ___ |
| Azure Database for PostgreSQL | giờ chạy của máy chủ + dung lượng + backup | giờ chạy × giá gói + GB × giá | ___ | ___ |
| Blob Storage | GB lưu, số thao tác ghi/đọc | (J × S + J × s × P) × E/30 + thao tác | ___ | ___ |
| Service Bus | số thao tác hoặc gói | theo gói (Basic hay Standard) | ___ | ___ |
| Container Registry | gói + dung lượng | gói Basic | ___ | ___ |
| Key Vault | số thao tác | thường nhỏ | ___ | ___ |
| Log Analytics | GB log nạp vào | GB/ngày × 30 | ___ | ___ |
| Egress (dữ liệu ra internet) | GB | J × s × P × d | ___ | ___ |
| **Tổng** | | | | **___** |

## 4. Chi phí mỗi lần chuyển đổi thành công

```
Chi phí mỗi job thành công = Tổng chi phí tháng / (J × s)
```

Ghi cả hai giá trị: **chi phí hạ tầng cố định** (Postgres, replica tối thiểu) và **chi phí biến đổi** (Jobs, storage, egress), vì phần cố định thường chiếm đa số ở quy mô nhỏ.

## 5. So sánh với credit

| Mục | Giá trị |
|---|---|
| Credit còn lại | ___ USD |
| Chi phí ước tính đến 2/12 (số ngày × chi phí/ngày khi chạy) | ___ USD |
| Còn lại sau đồ án | ___ USD |

## 6. Cách giữ chi phí trong tầm kiểm soát

- Đặt **budget và cảnh báo** (50% và 80% credit) trong Cost Management. Cảnh báo **không dừng** tài nguyên, nên phải tự tắt.
- Postgres và Container Apps tính tiền theo giờ chạy: **dừng hoặc xoá khi không test**.
- Bounded scale: đặt `maxReplicas` thấp cho API và Jobs.
- Load test có thời lượng và mức đồng thời cố định, ghi trước khi chạy.
- Xoá tài nguyên sau khi chụp đủ bằng chứng và được chấm (xem hướng dẫn dọn dẹp, `terraform destroy`), rồi **kiểm tra không còn tài nguyên tính phí**.

## 7. Việc cần làm

- [ ] Như: chốt vùng, gói Postgres, cấu hình Container Apps
- [ ] Muội, Như: điền giá từ Pricing Calculator vào mục 3
- [ ] Sau load test: thay giả định mục 2 bằng số đo thật
