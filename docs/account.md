# Azure Account, Region & Quota Verification (Doc2PDF)

## 1. Thông tin tài khoản & Ngân sách
- **Subscription:** Azure for Students (Active — quỹ $100)
- **Billing / Cost Management:** Đã thiết lập Budget `budget-doc2pdf` giới hạn **$30.00 / tháng** (reset hàng tháng) kèm cảnh báo qua email tại các mốc **50%**, **80%**, **100%** (Actual) và **100%** (Forecasted).

## 2. Danh sách Region được cấp phép & Vùng triển khai chính
Tài khoản Azure for Students bị giới hạn bởi Azure Policy và chỉ được phép tạo tài nguyên tại **5 region** sau:
- `japaneast` (**Region triển khai chính của dự án**)
- `koreacentral` (Region dự phòng)
- `malaysiawest`
- `indonesiacentral`
- `indiasouthcentral`

*(Lưu ý: Không chọn các region mặc định như `Australia East`, `East US` hay `Southeast Asia` vì sẽ bị chặn bởi lỗi `RequestDisallowedByAzure`).*

## 3. Kết quả kiểm tra Quota & Dịch vụ cốt lõi (tại `Japan East`)
Đã triển khai thử nghiệm và xác nhận tài khoản đủ quyền + đủ quota cho toàn bộ kiến trúc Doc2PDF:

| Dịch vụ | Cấu hình đã kiểm tra | Trạng thái | Lưu ý cấu hình khi deploy |
| :--- | :--- | :--- | :--- |
| **Storage Account** | Standard / LRS (Blob Storage) | Đạt | Tên tài khoản phải là chữ thường + số, duy nhất toàn cầu. |
| **Service Bus** | Basic Tier | Đạt | Hỗ trợ tốt hàng đợi job giữa `relay` và `worker`. |
| **PostgreSQL Flexible Server** | Workload `Dev/Test` — Burstable `Standard_B1ms` (32 GiB) | Đạt | Bắt buộc chuyển sang `Dev/Test` để chọn gói `B1ms`; chọn `PostgreSQL authentication only`. |
| **Container Apps** | Consumption Plan (cho `api`, `worker`, `relay`) | Đạt | Xem lưu ý về Logs destination bên dưới. |

## 4. Các lỗi cấu hình đã gặp & Cách khắc phục cho nhóm
1. **Lỗi `ExpressAzureMonitorLogDestNotSupported` khi tạo Container Apps:**
   - **Nguyên nhân:** Giao diện tạo nhanh mặc định dùng *Express Environment* nhưng lại bật xuất log sang *Azure Monitor*.
   - **Cách xử lý:** Khi tạo **Container Apps Environment**, chuyển sang tab **Monitoring** và đổi **Logs destination** thành **`Don't save logs`** (hoặc tạo **Log Analytics** riêng).
2. **Quy tắc tiết kiệm chi phí ($100 Credit):**
   - **PostgreSQL Flexible Server** là dịch vụ tốn chi phí cố định nhiều nhất (~$15–$18/tháng). Sau khi nhóm dev/test xong trong ngày, nên vào trang quản lý PostgreSQL bấm **Stop** (tạm dừng tối đa 7 ngày, không tính phí Compute trong thời gian tắt).
