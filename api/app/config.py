from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Hạ tầng (trùng tên biến với .env.example)
    database_url: str = "postgresql://doc2pdf:doc2pdf_dev_password@localhost:5432/doc2pdf"
    s3_endpoint: str = "http://localhost:8333"
    s3_public_endpoint: str | None = None  # host mà trình duyệt truy cập được (để ký URL tải về)
    s3_access_key: str = "any"
    s3_secret_key: str = "any"
    s3_region: str = "us-east-1"
    s3_bucket_sources: str = "sources"
    s3_bucket_results: str = "results"
    max_upload_mb: int = 10

    # Xác thực
    jwt_secret: str = "dev-only-change-me"
    jwt_ttl_minutes: int = 120
    demo_accounts: str = "alice:alice123:user,bob:bob123:user,operator:operator123:operator"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Worker gọi /api/internal/* bằng header X-Service-Key (cùng tên biến, cùng mặc định với worker/)
    internal_service_key: str = "dev-internal-key"
    converter_version: str = "doc2pdf-converters-1"  # ghi vào ResultObject khi worker không báo phiên bản

    # Job / tải về
    max_attempts: int = 3  # ngân sách thử ghi vào job khi tạo / retry
    result_ttl_hours: int = 24  # PDF hết hạn tải sau thời gian này
    download_url_ttl_seconds: int = 600  # 10 phút (theo docs/test-plan.md)

    @field_validator("database_url")
    @classmethod
    def _force_psycopg2(cls, v: str) -> str:
        # .env.example dùng postgresql:// (không nêu driver); ép dùng psycopg2 cho mọi phiên bản SQLAlchemy
        for prefix in ("postgresql://", "postgres://"):
            if v.startswith(prefix):
                return "postgresql+psycopg2://" + v[len(prefix):]
        return v

    @property
    def upload_limit_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def accounts(self) -> dict[str, tuple[str, str]]:
        """username -> (password, role)"""
        out: dict[str, tuple[str, str]] = {}
        for item in self.demo_accounts.split(","):
            parts = item.strip().split(":")
            if len(parts) == 3:
                out[parts[0]] = (parts[1], parts[2])
        return out

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
