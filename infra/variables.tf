variable "project_name" {
  description = "Ten du an (tien to dat ten tai nguyen)"
  type        = string
  default     = "doc2pdf"
}

variable "environment" {
  description = "Moi truong trien khai (dev, prod)"
  type        = string
  default     = "dev"
}

variable "location" {
  description = "Azure Region duoc cap phep cho Azure for Students"
  type        = string
  default     = "japaneast"

  validation {
    condition = contains([
      "japaneast",
      "koreacentral",
      "malaysiawest",
      "indonesiacentral",
      "indiasouthcentral"
    ], var.location)
    error_message = "Region phai thuoc 5 vung duoc mo khoa cho goi Azure for Students."
  }
}

variable "pg_admin_username" {
  description = "Tai khoan quan tri PostgreSQL"
  type        = string
  default     = "doc2pdfadmin"
}

variable "pg_admin_password" {
  description = "Mat khau quan tri PostgreSQL (toi thieu 8 ky tu, co chu hoa, chu thuong, so)"
  type        = string
  sensitive   = true
}
