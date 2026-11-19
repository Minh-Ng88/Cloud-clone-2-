# api

FastAPI + PostgreSQL + S3. Swagger: `http://localhost:8000/docs`. Chạy và kiểm thử: xem cuối file.

## Phân quyền (kiểm tra ở API, không dựa vào giao diện)

| Ai | Được làm |
|---|---|
| Không đăng nhập | `/health`, `/ready`, `POST /auth/login`; còn lại `401` |
| `user` | `POST /jobs`, `GET /jobs`, xem / tải / retry **job của mình**; job người khác: xem và tải trả `404`, retry trả `403`; `/operator/*` trả `403` |
| `operator` | Như user, cộng thêm xem và retry mọi job, `GET /operator/jobs`, `/operator/stats`, `/operator/outbox` |
| worker | Chỉ `/api/internal/jobs/*` bằng header `X-Service-Key` (biến `INTERNAL_SERVICE_KEY`, mặc định `dev-internal-key`); token người dùng, kể cả operator, bị từ chối `401` |

## Luồng

1. `POST /jobs` (multipart `file`, `options`): kiểm tra đuôi, rỗng, ≤ 10 MB và magic bytes; ghi file vào bucket riêng tư `sources/jobs/{job_id}/{tên}.{đuôi}`; **một giao dịch** ghi `conversion_jobs` + `source_objects` + `outbox_events`; trả `202`.
2. `relay/` đọc `outbox_events` chưa gửi và publish vào queue `doc2pdf.jobs` (worker nghe queue này).
3. Worker gọi `/api/internal/jobs/{id}/...` (bảng dưới), ghi PDF vào `results/jobs/{job_id}/attempt-{n}.pdf`.
4. `GET /jobs/{id}` xem trạng thái; `GET /jobs/{id}/download` trả URL ký sống 10 phút; `POST /jobs/{id}/retry` chạy lại job `failed` (đặt lại `attempts = 0`).

## Giao thức worker (`/api/internal/jobs/{id}/...`, header `X-Service-Key`)

| Endpoint | Body | Kết quả |
|---|---|---|
| `POST claim` | `worker_id`, `message_id`, `lease_seconds` | `200` + `status`, `attempts`, `attempt_no`, `source_bucket`, `source_key`, `result_bucket`, `result_key`, … Job → `running`, đặt `lease_expires_at`, `attempts + 1`. `409` kèm `detail`: `Duplicate message_id…`, `Job already finished (…)` (worker ack, bỏ qua) hoặc `Lease held by another worker…` (worker requeue). `404` nếu không có job |
| `POST heartbeat` | `worker_id`, `lease_seconds` | `200` gia hạn lease; `409` nếu worker không còn giữ job |
| `POST complete` | `worker_id`, `message_id`, `result_key`, `page_count`, `file_size` | API kiểm PDF có trong `results` (đúng khoá, đúng kích thước, header `%PDF-`), tự tính SHA-256, ghi `ResultObject` (hết hạn sau 24 h) → `succeeded`. `422` nếu object thiếu hoặc sai; gọi lại là idempotent |
| `POST fail` | `worker_id`, `message_id`, `error_code`, `error_message`, `retryable` | `retryable` và còn lượt → `queued` (worker nack-requeue chính message đó; API **không** ghi thêm outbox); ngược lại `failed` |

* Khử trùng lặp: `message_id` lưu trong `conversion_attempts` (UNIQUE); nhả khi retry tự động để message đó được nhận lại.
* Mã lỗi worker được chuẩn hoá: `INVALID_INPUT`→`INVALID_FILE`, `CONVERT_TIMEOUT`→`CONVERSION_TIMEOUT`, `INVALID_OUTPUT_PDF`→`OUTPUT_INVALID`, `WORKER_ERROR`→`INTERNAL_ERROR`.
* Worker chết giữa chừng: relay thu hồi lease hết hạn (về `queued` kèm outbox mới, hoặc `failed` nếu hết lượt).

## Message trong queue

`{schema_version, message_id, type: "job.convert.requested", job_id, source_key, source_bucket, format, request_id, enqueued_at}`; AMQP `message_id` = `message_id`.

## Chạy và kiểm thử

```bash
docker compose up -d --build
docker compose exec api python scripts/smoke_test.py        # toàn tuyến (tắt worker thật trước khi chạy)
docker compose --profile test run --rm api-test              # test tự động
```
