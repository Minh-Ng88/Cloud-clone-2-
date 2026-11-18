# Hạ tầng Azure (Terraform) — Doc2PDF

Thư mục này khởi tạo 5 tài nguyên lõi của dự án trên Azure (`Japan East`):
1. **Resource Group** (`rg-doc2pdf-dev`)
2. **Azure Container Registry** (`Basic` SKU)
3. **Storage Account** (`Standard_LRS`) kèm 2 private container: `sources`, `results`
4. **PostgreSQL Flexible Server** (`B_Standard_B1ms`, database `doc2pdf`)
5. **Azure Key Vault** lưu tự động `database-url`, `storage-connection-string`, `acr-admin-password`, `jwt-secret`

## Cách chạy trên máy cục bộ

```bash
az login
cd infra
cp terraform.tfvars.example terraform.tfvars
# Mở terraform.tfvars và đổi pg_admin_password thành mật khẩu mạnh của bạn

terraform init
terraform validate
terraform plan
terraform apply
```

Khi không sử dụng, có thể tạm dừng PostgreSQL trên Azure Portal hoặc chạy `terraform destroy` để tiết kiệm quỹ $100.
