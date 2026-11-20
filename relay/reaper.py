"""Thu hồi lease: job `running` mà `lease_expires_at` đã qua (worker chết/treo) được đưa về `queued`
(kèm sự kiện outbox mới) hoặc `failed` nếu hết `max_attempts`. Đây là bước "phục hồi" của Test 3.

Idempotent và an toàn khi nhiều tiến trình cùng chạy (FOR UPDATE SKIP LOCKED); worker cũng có thể
tự thu hồi theo cùng quy tắc (docs/contract.md §3). Dùng đồng hồ của PostgreSQL (now()).
"""
import logging
import uuid

from sqlalchemy import text

log = logging.getLogger("relay.reaper")


def reclaim_expired(engine, limit: int = 50) -> tuple[int, int]:
    """Trả (số job về queued, số job failed)."""
    requeued = failed = 0
    with engine.begin() as c:
        ids = c.execute(text("""SELECT id FROM conversion_jobs WHERE status = 'running'
                                AND lease_expires_at < now() ORDER BY lease_expires_at LIMIT :n
                                FOR UPDATE SKIP LOCKED"""), {"n": limit}).scalars().all()
        for jid in ids:
            c.execute(text("""UPDATE conversion_attempts SET outcome = 'lease_expired', finished_at = now(),
                              error_code = 'LEASE_EXPIRED', error_message = 'Worker không gia hạn lease'
                              WHERE job_id = :j AND outcome = 'running'"""), {"j": jid})
            status = c.execute(text("""
                UPDATE conversion_jobs SET
                  status = CASE WHEN attempts < max_attempts THEN 'queued' ELSE 'failed' END,
                  finished_at = CASE WHEN attempts < max_attempts THEN NULL ELSE now() END,
                  worker_id = NULL, lease_expires_at = NULL, error_code = 'LEASE_EXPIRED',
                  error_message = 'Worker không gia hạn lease đúng hạn', updated_at = now()
                WHERE id = :j RETURNING status"""), {"j": jid}).scalar_one()
            if status == "queued":
                mid = str(uuid.uuid4())
                c.execute(text("""
                    INSERT INTO outbox_events (id, aggregate_id, event_type, payload, created_at, available_at)
                    SELECT CAST(:mid AS uuid), j.id, 'job.convert.requested',
                           jsonb_build_object('schema_version', 1, 'message_id', CAST(:mid AS text),
                                              'type', 'job.convert.requested', 'job_id', CAST(j.id AS text),
                                              'source_key', s.object_key, 'source_bucket', s.bucket,
                                              'format', j.source_format,
                                              'request_id', j.request_id, 'enqueued_at', now()),
                           now(), now()
                    FROM conversion_jobs j JOIN source_objects s ON s.job_id = j.id WHERE j.id = :j"""),
                    {"mid": mid, "j": jid})
                requeued += 1
            else:
                failed += 1
            log.warning("lease expired job=%s -> %s", jid, status)
    return requeued, failed
