"""Truy cập object storage (S3 / SeaweedFS). Đối tượng luôn riêng tư; tải về bằng URL ký có hạn."""
import logging
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import get_settings

log = logging.getLogger("app.storage")


class Storage:
    def __init__(self) -> None:
        s = get_settings()
        self.s = s
        cfg = Config(signature_version="s3v4", s3={"addressing_style": "path"},
                     retries={"max_attempts": 3}, connect_timeout=3, read_timeout=10)
        kw = dict(aws_access_key_id=s.s3_access_key, aws_secret_access_key=s.s3_secret_key,
                  region_name=s.s3_region, config=cfg)
        self.client = boto3.client("s3", endpoint_url=s.s3_endpoint, **kw)
        # Client riêng chỉ để ký URL với host mà trình duyệt truy cập được
        self.signer = boto3.client("s3", endpoint_url=s.s3_public_endpoint or s.s3_endpoint, **kw)

    def ensure_buckets(self) -> None:
        for bucket in (self.s.s3_bucket_sources, self.s.s3_bucket_results):
            try:
                self.client.head_bucket(Bucket=bucket)
            except ClientError:
                self.client.create_bucket(Bucket=bucket)
                log.info("created bucket %s", bucket)

    def put_source(self, key: str, data: bytes, content_type: str | None) -> None:
        self.client.put_object(Bucket=self.s.s3_bucket_sources, Key=key, Body=data,
                               ContentType=content_type or "application/octet-stream")

    def delete(self, bucket: str, key: str) -> None:
        self.client.delete_object(Bucket=bucket, Key=key)

    def inspect_object(self, bucket: str, key: str, max_bytes: int = 64 * 1024 * 1024) -> dict | None:
        """Đọc object để lấy kích thước, SHA-256 và kiểm tra header PDF. None nếu không tồn tại."""
        import hashlib
        try:
            body = self.client.get_object(Bucket=bucket, Key=key)["Body"]
        except ClientError:
            return None
        h, size, head = hashlib.sha256(), 0, b""
        for chunk in body.iter_chunks(1024 * 1024):
            if not head:
                head = chunk[:5]
            size += len(chunk)
            if size > max_bytes:
                break
            h.update(chunk)
        return {"size": size, "sha256": h.hexdigest(), "is_pdf": head == b"%PDF-"}

    def presign_download(self, bucket: str, key: str, filename: str, ttl: int) -> str:
        disposition = f"attachment; filename=\"result.pdf\"; filename*=UTF-8''{quote(filename)}"
        return self.signer.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key, "ResponseContentType": "application/pdf",
                    "ResponseContentDisposition": disposition},
            ExpiresIn=ttl,
        )

    def ping(self) -> bool:
        try:
            self.client.head_bucket(Bucket=self.s.s3_bucket_sources)
            return True
        except Exception:
            return False


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = Storage()
    return _storage
