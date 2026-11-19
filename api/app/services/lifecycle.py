"""Vòng đời job phía worker (qua /api/internal/*): claim / heartbeat / complete / fail.

Quy tắc chung:
* Mọi hàm khoá dòng job (SELECT ... FOR UPDATE) và chỉ chấp nhận worker đang giữ job
  (status = running, worker_id khớp, message_id khớp lần xử lý đang chạy) — "fencing".
* Retry tự động do worker thực hiện bằng cách nack-requeue chính message đó, nên API chỉ đưa job về
  `queued` (không ghi thêm outbox). Outbox mới chỉ được ghi khi tạo job, retry thủ công
  (jobs.retry_job) và khi relay thu hồi lease hết hạn.
"""
import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import ApiError
from app.models import (FAILED, QUEUED, RUNNING, SUCCEEDED, ConversionAttempt, ConversionJob,
                        ResultObject)
from app.schemas import ClaimIn, ClaimOut, CompleteIn, FailIn
from app.services.jobs import enqueue_job
from app.storage import Storage
from app.timeutil import aware, now_utc

log = logging.getLogger("app.lifecycle")

# Worker dùng mã lỗi riêng; API chuẩn hoá về bộ mã đã công bố (frontend, seed, test-plan dựa vào bộ này).
CANONICAL_CODES = {"INVALID_FILE", "UNSUPPORTED_FORMAT", "FILE_TOO_LARGE", "CONVERSION_TIMEOUT",
                   "CONVERSION_ERROR", "OUTPUT_INVALID", "LEASE_EXPIRED", "INTERNAL_ERROR"}
CODE_ALIASES = {"INVALID_INPUT": "INVALID_FILE", "CONVERT_TIMEOUT": "CONVERSION_TIMEOUT",
                "INVALID_OUTPUT_PDF": "OUTPUT_INVALID", "WORKER_ERROR": "INTERNAL_ERROR",
                "MISSING_SOURCE_KEY": "INTERNAL_ERROR"}


def normalize_error_code(code: str) -> str:
    code = (code or "").strip().upper()
    code = CODE_ALIASES.get(code, code)
    return code if code in CANONICAL_CODES else "INTERNAL_ERROR"


def result_key(job_id: uuid.UUID, attempt_no: int) -> str:
    return f"jobs/{job_id}/attempt-{attempt_no}.pdf"


def _lock(db: Session, job_id: uuid.UUID) -> ConversionJob:
    job = db.execute(select(ConversionJob).where(ConversionJob.id == job_id)
                     .with_for_update()).scalar_one_or_none()
    if job is None:
        raise ApiError(404, "JOB_NOT_FOUND", "Không tìm thấy job", detail="Job not found")
    return job


def _lease(requested: int | None) -> int:
    return min(requested or 60, 600)


def _mid(value: str | None) -> uuid.UUID | None:
    """message_id của broker có thể không phải UUID: ánh xạ ổn định về UUID để lưu/so khớp."""
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, str(value))


def _running_attempt(db: Session, job: ConversionJob) -> ConversionAttempt | None:
    return db.execute(select(ConversionAttempt).where(
        ConversionAttempt.job_id == job.id, ConversionAttempt.outcome == "running")
        .order_by(ConversionAttempt.attempt_no.desc()).limit(1)).scalar_one_or_none()


def _fence(db: Session, job: ConversionJob, worker_id: str, message_id: str | None) -> ConversionAttempt:
    att = _running_attempt(db, job)
    ok = job.status == RUNNING and job.worker_id == worker_id and att is not None
    if ok and message_id and att.message_id is not None and att.message_id != _mid(message_id):
        ok = False
    if not ok:
        raise ApiError(409, "LEASE_LOST", "Worker không còn giữ lease của job này",
                       {"status": job.status}, detail="Lease lost: job is not running under this worker")
    return att


def _expire_running(db: Session, job: ConversionJob, enqueue: bool = True) -> str:
    """Lease hết hạn: đóng attempt đang chạy, đưa job về queued (còn lượt) hoặc failed."""
    now = now_utc()
    att = _running_attempt(db, job)
    if att:
        att.outcome, att.finished_at = "lease_expired", now
        att.error_code, att.error_message = "LEASE_EXPIRED", "Worker không gia hạn lease đúng hạn"
    job.worker_id = job.lease_expires_at = None
    job.error_code, job.error_message = "LEASE_EXPIRED", "Worker không gia hạn lease đúng hạn"
    if job.attempts < job.max_attempts:
        job.status = QUEUED
        if enqueue:
            enqueue_job(db, job)
    else:
        job.status, job.finished_at = FAILED, now
    log.warning("lease expired job=%s attempts=%d -> %s", job.id, job.attempts, job.status)
    return job.status


# ------------------------------------------------------------------ claim
def claim(db: Session, job_id: uuid.UUID, body: ClaimIn) -> ClaimOut:
    s = get_settings()
    job = _lock(db, job_id)
    now = now_utc()

    mid = _mid(body.message_id)
    if mid and db.scalar(select(ConversionAttempt.id).where(ConversionAttempt.message_id == mid)):
        raise ApiError(409, "DUPLICATE_MESSAGE", "Message này đã được xử lý", {"status": job.status},
                       detail="Duplicate message_id: message was already claimed")
    if job.status in (SUCCEEDED, FAILED):
        raise ApiError(409, "JOB_ALREADY_FINISHED", "Job đã kết thúc", {"status": job.status},
                       detail=f"Job already finished ({job.status})")
    if job.status == RUNNING:
        if aware(job.lease_expires_at) > now:
            raise ApiError(409, "LEASE_HELD", "Job đang được worker khác xử lý",
                           {"lease_expires_at": aware(job.lease_expires_at).isoformat()},
                           detail="Lease held by another worker, retry later")
        if _expire_running(db, job, enqueue=False) == FAILED:  # lease cũ hết hạn và hết lượt thử
            db.commit()
            raise ApiError(409, "JOB_ALREADY_FINISHED", "Job đã hết lượt thử", {"status": FAILED},
                           detail="Job already finished (failed): attempts exhausted")
    if job.attempts >= job.max_attempts:
        job.status, job.finished_at = FAILED, now
        job.error_code = job.error_code or "INTERNAL_ERROR"
        db.commit()
        raise ApiError(409, "JOB_ALREADY_FINISHED", "Job đã hết lượt thử", {"status": FAILED},
                       detail="Job already finished (failed): attempts exhausted")

    attempt_no = (db.scalar(select(func.max(ConversionAttempt.attempt_no))
                            .where(ConversionAttempt.job_id == job.id)) or 0) + 1
    lease = _lease(body.lease_seconds)
    job.attempts += 1
    job.status, job.worker_id = RUNNING, body.worker_id
    job.lease_expires_at = now + timedelta(seconds=lease)
    job.started_at = job.started_at or now
    job.error_code = job.error_message = None
    db.add(ConversionAttempt(job_id=job.id, attempt_no=attempt_no, message_id=mid,
                             worker_id=body.worker_id, outcome="running", started_at=now,
                             lease_expires_at=job.lease_expires_at))
    db.commit()
    log.info("claimed job=%s attempt=%d worker=%s", job.id, attempt_no, body.worker_id)
    src = job.source
    return ClaimOut(
        job_id=job.id, status=job.status, attempts=job.attempts, attempt_no=attempt_no,
        max_attempts=job.max_attempts, lease_expires_at=job.lease_expires_at,
        source_format=job.source_format, original_filename=job.original_filename,
        options=job.options or {}, source_bucket=src.bucket, source_key=src.object_key,
        source_size_bytes=src.size_bytes, source_checksum=src.source_checksum,
        result_bucket=s.s3_bucket_results, result_key=result_key(job.id, attempt_no))


# ------------------------------------------------------------------ heartbeat
def heartbeat(db: Session, job_id: uuid.UUID, worker_id: str, lease_seconds: int | None):
    job = _lock(db, job_id)
    att = _fence(db, job, worker_id, None)
    job.lease_expires_at = now_utc() + timedelta(seconds=_lease(lease_seconds))
    att.lease_expires_at = job.lease_expires_at
    db.commit()
    return job


# ------------------------------------------------------------------ complete
def complete(db: Session, storage: Storage, job_id: uuid.UUID, body: CompleteIn) -> ConversionJob:
    s = get_settings()
    job = _lock(db, job_id)
    if job.status == SUCCEEDED and job.worker_id == body.worker_id and job.result is not None:
        return job  # idempotent: gọi lại sau khi đã thành công
    att = _fence(db, job, body.worker_id, body.message_id)

    expected = result_key(job.id, att.attempt_no)
    if body.result_key != expected:
        raise ApiError(422, "INVALID_RESULT_KEY", "result_key không đúng khoá được cấp khi claim",
                       {"expected": expected}, detail="result_key does not match the key issued at claim")
    info = storage.inspect_object(s.s3_bucket_results, expected)
    if info is None or info["size"] != body.file_size or not info["is_pdf"]:
        raise ApiError(422, "RESULT_OBJECT_INVALID",
                       "Không thấy object PDF kết quả, sai kích thước hoặc không phải PDF",
                       {"key": expected, "found": None if info is None else {
                           "size": info["size"], "is_pdf": info["is_pdf"]}, "declared_size": body.file_size},
                       detail="Result PDF object is missing or invalid")
    now = now_utc()
    version = body.converter_version or s.converter_version
    job.result = ResultObject(
        bucket=s.s3_bucket_results, object_key=expected, size_bytes=info["size"],
        result_checksum=info["sha256"], page_count=body.page_count, converter_version=version,
        created_at=now, expires_at=now + timedelta(hours=s.result_ttl_hours))
    job.status, job.finished_at = SUCCEEDED, now
    job.lease_expires_at = None
    job.converter_version = version
    job.error_code = job.error_message = None
    att.outcome, att.finished_at = "succeeded", now
    att.converter_version, att.duration_ms = version, body.duration_ms
    db.commit()
    log.info("succeeded job=%s attempt=%d pages=%d", job.id, att.attempt_no, body.page_count)
    return job


# ------------------------------------------------------------------ fail
def fail(db: Session, job_id: uuid.UUID, body: FailIn) -> ConversionJob:
    job = _lock(db, job_id)
    att = _fence(db, job, body.worker_id, body.message_id)
    now = now_utc()
    code = normalize_error_code(body.error_code)
    msg = body.error_message[:2000]
    att.outcome, att.finished_at = "failed", now
    att.error_code, att.error_message = code, msg
    att.converter_version, att.duration_ms = body.converter_version, body.duration_ms
    job.error_code, job.error_message = code, msg
    job.converter_version = body.converter_version or job.converter_version
    job.lease_expires_at = None
    if body.retryable and job.attempts < job.max_attempts:
        job.status, job.worker_id = QUEUED, None
        att.message_id = None  # worker sẽ nack-requeue đúng message này để thử lại: nhả khoá khử trùng lặp
    else:
        job.status, job.finished_at = FAILED, now
    db.commit()
    log.info("fail job=%s attempt=%d code=%s -> %s", job.id, att.attempt_no, code, job.status)
    return job
