"""Giao thức worker <-> API. Chỉ chấp nhận header X-Service-Key; người dùng không gọi được."""
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import (ClaimIn, ClaimOut, CompleteIn, FailIn, HeartbeatIn, HeartbeatOut, JobOut,
                         job_to_out)
from app.security import require_service
from app.services import lifecycle
from app.storage import get_storage

router = APIRouter(prefix="/jobs", tags=["internal"], dependencies=[Depends(require_service)])


@router.post("/{job_id}/claim", response_model=ClaimOut)
def claim(job_id: uuid.UUID, body: ClaimIn, db: Session = Depends(get_db)):
    return lifecycle.claim(db, job_id, body)


@router.post("/{job_id}/heartbeat", response_model=HeartbeatOut)
def heartbeat(job_id: uuid.UUID, body: HeartbeatIn, db: Session = Depends(get_db)):
    job = lifecycle.heartbeat(db, job_id, body.worker_id, body.lease_seconds)
    return HeartbeatOut(status=job.status, lease_expires_at=job.lease_expires_at)


@router.post("/{job_id}/complete", response_model=JobOut)
def complete(job_id: uuid.UUID, body: CompleteIn, db: Session = Depends(get_db)):
    return job_to_out(lifecycle.complete(db, get_storage(), job_id, body))


@router.post("/{job_id}/fail", response_model=JobOut)
def fail(job_id: uuid.UUID, body: FailIn, db: Session = Depends(get_db)):
    return job_to_out(lifecycle.fail(db, job_id, body))
