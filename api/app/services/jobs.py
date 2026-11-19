"""Tạo job, truy vấn, retry. Mọi thay đổi job + outbox_events nằm trong CÙNG một giao dịch."""
import hashlib
import logging
import os
import re
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import ApiError
from app.models import FAILED, QUEUED, ConversionJob, OutboxEvent, SourceObject
from app.schemas import ConversionOptions
from app.security import Principal
from app.storage import Storage
from app.timeutil import now_utc
from app.validation import validate_content

log = logging.getLogger("app.jobs")

EXT_TO_FORMAT = {"txt": "txt", "md": "md", "markdown": "md", "png": "png", "jpg": "jpg", "jpeg": "jpg"}
MESSAGE_TYPE = "job.convert.requested"


def source_object_key(job_id: uuid.UUID, filename: str, fmt: str) -> str:
    """Khoá nguồn giữ tên gốc (đã làm sạch) và đuôi chuẩn hoá: worker chọn bộ chuyển đổi theo đuôi file
    của source_key, và nhận diện file demo (vd slow_case.txt) theo tên."""
    stem = os.path.splitext(filename)[0]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")[:80] or "source"
    return f"jobs/{job_id}/{stem}.{fmt}"


def clean_filename(name: str | None) -> str:
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return base[:255] or "file"


def detect_format(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    fmt = EXT_TO_FORMAT.get(ext)
    if not fmt:
        raise ApiError(415, "UNSUPPORTED_FORMAT",
                       "Chỉ hỗ trợ .txt, .md, .png, .jpg/.jpeg",
                       {"allowed_extensions": sorted(EXT_TO_FORMAT)})
    return fmt


def enqueue_job(db: Session, job: ConversionJob, delay_seconds: float = 0) -> OutboxEvent:
    """Ghi sự kiện outbox (message_id = outbox.id). Relay sẽ đẩy sang RabbitMQ."""
    now = now_utc()
    mid = uuid.uuid4()
    ev = OutboxEvent(
        id=mid, aggregate_id=job.id, event_type=MESSAGE_TYPE, created_at=now,
        available_at=now + timedelta(seconds=delay_seconds),
        payload={
            "schema_version": 1, "message_id": str(mid), "type": MESSAGE_TYPE,
            "job_id": str(job.id), "source_key": job.source.object_key,
            "source_bucket": job.source.bucket, "format": job.source_format, "request_id": job.request_id,
            "enqueued_at": now.isoformat(),
        },
    )
    db.add(ev)
    return ev


def create_job(db: Session, storage: Storage, owner_id: str, filename: str | None,
               content_type: str | None, data: bytes, options: ConversionOptions,
               request_id: str | None) -> ConversionJob:
    s = get_settings()
    filename = clean_filename(filename)
    fmt = detect_format(filename)
    if not data:
        raise ApiError(400, "EMPTY_FILE", "File rỗng")
    if len(data) > s.upload_limit_bytes:
        raise ApiError(413, "FILE_TOO_LARGE", f"File vượt quá {s.max_upload_mb} MB")
    validate_content(fmt, data)  # magic bytes / UTF-8: file giả đuôi bị từ chối, không tạo job

    job_id = uuid.uuid4()
    checksum = hashlib.sha256(data).hexdigest()
    key = source_object_key(job_id, filename, fmt)
    storage.put_source(key, data, content_type)  # ghi object trước, DB sau
    try:
        job = ConversionJob(
            id=job_id, owner_id=owner_id, status=QUEUED, source_format=fmt,
            original_filename=filename, options=options.model_dump(), attempts=0,
            max_attempts=s.max_attempts, source_checksum=checksum, request_id=request_id)
        job.source = SourceObject(bucket=s.s3_bucket_sources, object_key=key,
                                  content_type=content_type, size_bytes=len(data),
                                  source_checksum=checksum)
        db.add(job)
        db.flush()
        enqueue_job(db, job)
        db.commit()
    except Exception:
        db.rollback()
        try:
            storage.delete(s.s3_bucket_sources, key)  # dọn object mồ côi (best effort)
        except Exception:
            log.warning("could not delete orphan source object job=%s", job_id)
        raise
    log.info("job created job=%s owner=%s format=%s size=%d", job_id, owner_id, fmt, len(data))
    return job


def get_job_for(db: Session, job_id: uuid.UUID, p: Principal) -> ConversionJob:
    """Người dùng thường chỉ thấy job của mình (job người khác -> 404, không lộ sự tồn tại)."""
    job = db.get(ConversionJob, job_id)
    if job is None or (not p.is_operator and job.owner_id != p.user_id):
        raise ApiError(404, "JOB_NOT_FOUND", "Không tìm thấy job")
    return job


def retry_job(db: Session, job_id: uuid.UUID, p: Principal) -> ConversionJob:
    job = db.execute(select(ConversionJob).where(ConversionJob.id == job_id)
                     .with_for_update()).scalar_one_or_none()
    if job is None:
        raise ApiError(404, "JOB_NOT_FOUND", "Không tìm thấy job")
    if not p.is_operator and job.owner_id != p.user_id:
        raise ApiError(403, "FORBIDDEN", "Chỉ chủ job hoặc operator mới được thử lại job này")
    if job.status != FAILED:
        raise ApiError(409, "INVALID_STATE", "Chỉ job ở trạng thái failed mới retry được",
                       {"status": job.status})
    job.status = QUEUED
    job.attempts, job.max_attempts = 0, get_settings().max_attempts  # cấp lại ngân sách thử (lịch sử còn ở attempts)
    job.error_code = job.error_message = None
    job.worker_id = job.lease_expires_at = job.finished_at = None
    enqueue_job(db, job)
    db.commit()
    log.info("job retried job=%s by=%s", job.id, p.user_id)
    return job
