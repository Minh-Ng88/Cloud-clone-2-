# relay

Đọc `outbox_events` chưa gửi trong PostgreSQL và publish vào queue mà worker nghe.

* Chỉ đánh dấu `published_at` **sau khi** broker xác nhận (publisher confirm) → at-least-once; consumer khử trùng lặp bằng `message_id`.
* Nhiều relay chạy song song an toàn (`FOR UPDATE SKIP LOCKED`).
* Publish vào `QUEUE_NAME` (mặc định `doc2pdf.jobs`) — đúng queue `worker/` nghe. `QUEUE_PROVIDER` giống worker: `rabbitmq` (mặc định; default exchange, khai báo queue và DLQ `doc2pdf.jobs.dlq` y hệt worker) hoặc `azure` (Azure Service Bus, cần `AZURE_SERVICEBUS_CONNECTION_STRING`).
* Định kỳ (`REAPER_INTERVAL_SECONDS`, mặc định 10 s) thu hồi job `running` có lease hết hạn: về `queued` kèm outbox mới, hoặc `failed` nếu hết lượt thử.
* Message: `{schema_version, message_id, type, job_id, source_key, source_bucket, format, request_id, enqueued_at}`.

Biến môi trường: `DATABASE_URL`, `QUEUE_PROVIDER`, `RABBITMQ_URL`, `AZURE_SERVICEBUS_CONNECTION_STRING`, `QUEUE_NAME`, `RELAY_POLL_SECONDS`, `RELAY_BATCH_SIZE`, `REAPER_INTERVAL_SECONDS`.
