import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import ConversionJob
from app.timeutil import aware, now_utc

JobStatus = Literal["queued", "running", "succeeded", "failed"]


class ConversionOptions(BaseModel):
    """Tuỳ chọn chuyển đổi. Tuỳ chọn không áp dụng cho định dạng nguồn sẽ bị bỏ qua."""
    model_config = ConfigDict(extra="forbid")
    page_size: Literal["A4", "LETTER"] = "A4"
    orientation: Literal["portrait", "landscape"] = "portrait"
    font_size: int = Field(11, ge=8, le=24)
    margin_mm: int = Field(20, ge=5, le=40)


# ---------- Auth ----------
class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    user_id: str
    role: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


# ---------- Job ----------
class SourceOut(BaseModel):
    size_bytes: int
    content_type: str | None


class ResultOut(BaseModel):
    size_bytes: int
    page_count: int
    checksum: str
    expires_at: datetime
    expired: bool


class JobOut(BaseModel):
    id: uuid.UUID
    owner_id: str
    status: JobStatus
    source_format: str
    original_filename: str
    options: dict
    attempts: int
    max_attempts: int
    worker_id: str | None
    lease_expires_at: datetime | None
    source_checksum: str
    converter_version: str | None
    error_code: str | None
    error_message: str | None
    request_id: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    source: SourceOut | None
    result: ResultOut | None
    download_available: bool


class JobList(BaseModel):
    items: list[JobOut]
    total: int
    limit: int
    offset: int


class AttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    attempt_no: int
    worker_id: str
    outcome: str
    started_at: datetime
    lease_expires_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None
    converter_version: str | None
    duration_ms: int | None


class DownloadOut(BaseModel):
    download_url: str
    url_expires_in: int
    result_expires_at: datetime
    filename: str


def job_to_out(job: ConversionJob) -> JobOut:
    r = job.result
    result = None
    if r is not None:
        expired = r.deleted_at is not None or aware(r.expires_at) <= now_utc()
        result = ResultOut(size_bytes=r.size_bytes, page_count=r.page_count,
                           checksum=r.result_checksum, expires_at=r.expires_at, expired=expired)
    src = job.source
    return JobOut(
        id=job.id, owner_id=job.owner_id, status=job.status, source_format=job.source_format,
        original_filename=job.original_filename, options=job.options or {},
        attempts=job.attempts, max_attempts=job.max_attempts, worker_id=job.worker_id,
        lease_expires_at=job.lease_expires_at, source_checksum=job.source_checksum,
        converter_version=job.converter_version, error_code=job.error_code,
        error_message=job.error_message, request_id=job.request_id, created_at=job.created_at,
        updated_at=job.updated_at, started_at=job.started_at, finished_at=job.finished_at,
        source=SourceOut(size_bytes=src.size_bytes, content_type=src.content_type) if src else None,
        result=result,
        download_available=bool(result and not result.expired and job.status == "succeeded"),
    )


# ---------- Operator ----------
class StatsOut(BaseModel):
    jobs_by_status: dict[str, int]
    oldest_queued_age_seconds: float | None
    running_with_expired_lease: int
    pending_outbox_events: int
    oldest_pending_outbox_age_seconds: float | None


class OutboxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    aggregate_id: uuid.UUID
    event_type: str
    created_at: datetime
    available_at: datetime
    published_at: datetime | None
    publish_attempts: int
    last_error: str | None


# ---------- Internal (worker) ----------
class ClaimIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=128)
    message_id: str | None = Field(default=None, max_length=128)
    lease_seconds: int | None = Field(default=None, ge=5)


class ClaimOut(BaseModel):
    job_id: uuid.UUID
    status: str
    attempts: int          # lần thử trong đợt hiện tại (reset khi retry thủ công)
    attempt_no: int        # số thứ tự lần xử lý trong lịch sử của job
    max_attempts: int
    lease_expires_at: datetime
    source_format: str
    original_filename: str
    options: dict
    source_bucket: str
    source_key: str
    source_size_bytes: int
    source_checksum: str
    result_bucket: str
    result_key: str


class HeartbeatIn(BaseModel):
    worker_id: str
    lease_seconds: int | None = Field(default=None, ge=5)


class HeartbeatOut(BaseModel):
    status: str
    lease_expires_at: datetime


class CompleteIn(BaseModel):
    worker_id: str
    message_id: str | None = None
    result_key: str
    page_count: int = Field(ge=1)
    file_size: int = Field(gt=0)
    converter_version: str | None = Field(default=None, max_length=64)
    duration_ms: int | None = Field(default=None, ge=0)


class FailIn(BaseModel):
    worker_id: str
    message_id: str | None = None
    error_code: str = Field(max_length=64)
    error_message: str = Field(default="", max_length=4000)
    retryable: bool = False
    converter_version: str | None = Field(default=None, max_length=64)
    duration_ms: int | None = Field(default=None, ge=0)
