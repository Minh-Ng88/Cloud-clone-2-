"""Outbox relay: đọc outbox_events chưa gửi trong PostgreSQL và đẩy sang hàng đợi.
Đồng thời định kỳ thu hồi lease hết hạn (reaper.py) để job của worker đã chết được thử lại.

Bảo đảm at-least-once: chỉ đánh dấu published_at SAU KHI broker xác nhận (publisher confirm).
Nếu chết giữa chừng, sự kiện được gửi lại -> consumer khử trùng lặp bằng message_id.
Nhiều relay chạy song song an toàn nhờ FOR UPDATE SKIP LOCKED.
"""
import json
import logging
import os
import time

from sqlalchemy import create_engine, text

from publisher import PermanentPublishError, create_publisher
from reaper import reclaim_expired

log = logging.getLogger("relay")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://doc2pdf:doc2pdf_dev_password@localhost:5432/doc2pdf")
for _p in ("postgresql://", "postgres://"):
    if DATABASE_URL.startswith(_p):
        DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len(_p):]
POLL_SECONDS = float(os.getenv("RELAY_POLL_SECONDS", "1"))
BATCH_SIZE = int(os.getenv("RELAY_BATCH_SIZE", "50"))
REAPER_INTERVAL = float(os.getenv("REAPER_INTERVAL_SECONDS", "10"))  # chu kỳ thu hồi lease hết hạn

PENDING_SQL = text("""
    SELECT id, payload FROM outbox_events
    WHERE published_at IS NULL AND available_at <= now() AND publish_attempts < 20
    ORDER BY created_at LIMIT :n FOR UPDATE SKIP LOCKED
""")


def publish_batch(engine, publisher) -> int:
    sent = 0
    with engine.begin() as db:
        for ev_id, payload in db.execute(PENDING_SQL, {"n": BATCH_SIZE}).all():
            payload = json.loads(payload) if isinstance(payload, str) else payload
            try:
                publisher.publish(str(ev_id), payload)
            except PermanentPublishError as e:  # ghi nhận để operator thấy (GET /operator/outbox)
                db.execute(text("UPDATE outbox_events SET publish_attempts = publish_attempts + 1, "
                                "last_error = :e WHERE id = :id"), {"e": str(e)[:500], "id": ev_id})
                log.error("cannot publish message=%s: %s", ev_id, e)
                continue
            db.execute(text("UPDATE outbox_events SET published_at = now(), "
                            "publish_attempts = publish_attempts + 1, last_error = NULL WHERE id = :id"),
                       {"id": ev_id})
            sent += 1
            log.info("published message=%s type=%s job=%s", ev_id, payload.get("type"),
                     payload.get("job_id"))
    return sent


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("pika").setLevel(logging.WARNING)
    logging.getLogger("azure").setLevel(logging.WARNING)
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    publisher = create_publisher()
    last_reap = 0.0
    while True:
        try:
            if time.monotonic() - last_reap >= REAPER_INTERVAL:
                last_reap = time.monotonic()
                requeued, failed = reclaim_expired(engine)
                if requeued or failed:
                    log.warning("reclaimed expired leases: requeued=%d failed=%d", requeued, failed)
            if publisher.closed:
                publisher.connect()
                log.info("connected to queue (%s)", type(publisher).__name__)
            if publish_batch(engine, publisher) == 0:
                publisher.wait(POLL_SECONDS)
        except Exception as e:  # hàng đợi/DB tạm thời không sẵn sàng: đóng kết nối, thử lại (không mất sự kiện)
            log.warning("relay error (%s: %s); retrying", type(e).__name__, str(e)[:200])
            publisher.close()
            time.sleep(3)


if __name__ == "__main__":
    main()
