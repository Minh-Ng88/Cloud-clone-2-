# Nhật ký triển khai Azure & Ghi nhận quyền / quota (T6 20/11)

## 1. Hạ tầng đã khởi tạo bằng Terraform (`japaneast`)
- Resource Group: `rg-doc2pdf-dev`
- Azure Container Registry (`Basic` SKU)
- Azure Storage Account (`Standard_LRS`, 2 container private `sources`, `results`)
- Azure Database for PostgreSQL Flexible Server (`B_Standard_B1ms`, PostgreSQL 16)
- Azure Key Vault (`standard` SKU)
- Azure Service Bus (`Basic` SKU, queue `doc2pdf-jobs`)
- Azure Container Apps Environment (`cae-doc2pdf-dev`) + Container Apps (`api`, `relay`) + Container Apps Job (`worker`)

## 2. Các lỗi quyền / quota gói Azure for Students gặp phải & cách khắc phục
1. **Giới hạn Region cho gói Student (`RequestDisallowedByAzure`):**
   - *Nguyên nhân:* Subscription Azure for Students chỉ cấp phép triển khai tại 5 region (`japaneast`, `koreacentral`, `malaysiawest`, `indonesiacentral`, `indiasouthcentral`).
   - *Khắc phục:* Ràng buộc `location = "japaneast"` bằng `validation` trong `infra/variables.tf`.
2. **Lỗi Log Analytics mặc định trên Container Apps Environment (`ExpressAzureMonitorLogDestNotSupported`):**
   - *Nguyên nhân:* Tài khoản Student không được cấp quyền tự động tạo Express Azure Monitor workspace khi dựng Container Apps Environment.
   - *Khắc phục:* Cấu hình `logs_destination = "none"` trong `azurerm_container_app_environment`.
3. **Tối ưu Quota vCPU & Ngân sách $100:**
   - Cấu hình `min_replicas = 0` cho `api` và `min_executions = 0` (KEDA event-driven theo hàng đợi Service Bus) cho `worker` job để chỉ tiêu tốn tài nguyên khi có yêu cầu chuyển đổi file.
