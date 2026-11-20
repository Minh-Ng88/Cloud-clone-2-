# Hợp đồng hàng đợi và trạng thái công việc

`schema_version = 1`. **PostgreSQL là nguồn sự thật**; message chỉ đánh thức worker. Message có thể bị giao lặp hoặc đến muộn, nên trạng thái trong DB và các quy tắc dưới đây quyết định kết quả. Worker không kết nối DB: mọi thay đổi trạng thái đi qua `/api/internal/*` của API (§3).

## 1. Trạng thái công việc

| Từ | Sang | Sự kiện |
|---|---|---|
| — | `queued` | `POST /jobs` (`attempts = 0`, ghi outbox cùng giao dịch) |
| `queued` | `running` | `claim`: `attempts + 1`, đặt `worker_id`, `lease_expires_at` |
| `running` | `running` | `heartbeat` gia hạn `lease_expires_at` |
| `running` | `succeeded` | `complete`: PDF đã có trong bucket `results`; ghi `ResultObject`, `converter_version` |
| `running` | `queued` | `fail` với `retryable = true` và `attempts < max_attempts`; **không** ghi outbox, worker nack-requeue chính message đó |
| `running` | `queued` | lease hết hạn và `attempts < max_attempts` (relay thu hồi; ghi outbox mới) |
| `running` | `failed` | `fail` không thử lại được hoặc hết lượt; hoặc lease hết hạn khi đã hết lượt |
| `failed` | `queued` | `POST /jobs/{id}/retry` (`attempts = 0`, `max_attempts = 3`, ghi outbox mới) |

`succeeded` là trạng thái cuối. Mọi chuyển trạng thái nằm trong **một giao dịch** có khoá dòng job; job `running` quá lease không ở lại mãi (§4).

## 2. Message trong hàng đợi

| Thành phần | Giá trị |
|---|---|
| Queue | `QUEUE_NAME` (mặc định `doc2pdf.jobs`), durable |
| RabbitMQ (`QUEUE_PROVIDER=rabbitmq`) | publish qua default exchange, routing key = tên queue; dead-letter: `x-dead-letter-exchange=""`, `x-dead-letter-routing-key=doc2pdf.jobs.dlq` (relay và worker khai báo giống hệt) |
| Azure Service Bus (`QUEUE_PROVIDER=azure`) | queue cùng tên; dead-letter do Service Bus quản lý |
| Thuộc tính | `message_id` = `message_id` trong thân, `content_type=application/json`, persistent; header `x-request-id` |

```json
{ "schema_version": 1,
  "message_id": "330e0c09-8212-4337-9f9d-a132dd913fe8",
  "type": "job.convert.requested",
  "job_id": "92a14679-dc4d-4145-b745-50c616657853",
  "source_key": "jobs/92a14679-dc4d-4145-b745-50c616657853/bao-cao.txt",
  "source_bucket": "sources",
  "format": "txt",
  "request_id": "ca2985cbbc6f4235a17d0ec7d794efda",
  "enqueued_at": "2026-11-17T09:00:00.123456+00:00" }
```

`format` ∈ `txt` · `md` · `png` · `jpg`. Message không chứa nội dung file hay URL; tuỳ chọn chuyển đổi và checksum lấy từ phản hồi `claim`.

**Outbox**: API (và bước thu hồi lease của relay) ghi `outbox_events` cùng giao dịch với thay đổi job; `outbox_events.id` chính là `message_id`. Relay đọc sự kiện chưa gửi (`published_at IS NULL AND available_at <= now()`, `FOR UPDATE SKIP LOCKED`), publish có xác nhận của broker, rồi mới đặt `published_at`. Hệ quả: **at-least-once**, worker phải chịu được message trùng.

## 3. Giao thức worker với API

Mọi lời gọi: `POST /api/internal/jobs/{job_id}/{action}`, header `X-Service-Key` và `Content-Type: application/json`.

### 3.1 `claim`: nhận job và đặt lease

Body: `{"worker_id", "message_id", "lease_seconds": 60}`. `200`:

```json
{ "job_id": "…", "status": "running", "attempts": 1, "attempt_no": 1, "max_attempts": 3,
  "lease_expires_at": "…", "source_format": "txt", "original_filename": "bao-cao.txt", "options": {…},
  "source_bucket": "sources", "source_key": "jobs/…/bao-cao.txt", "source_size_bytes": 1832,
  "source_checksum": "9f86d081…", "result_bucket": "results", "result_key": "jobs/…/attempt-1.pdf" }
```
`attempts` đếm trong đợt hiện tại (worker dùng để quyết định còn thử lại được không); `attempt_no` đánh số lịch sử và nằm trong `result_key`.

| HTTP | `detail` (từ khoá worker nhận biết) | Worker làm gì |
|---|---|---|
| 409 | `Duplicate message_id…` (message đã claim) | **ack**, bỏ qua |
| 409 | `Job already finished (succeeded/failed)…` | **ack**, bỏ qua |
| 409 | `Lease held by another worker, retry later` | **nack requeue**, thử lại sau |
| 404 | `Job not found` | **ack**, bỏ qua |
| 5xx / lỗi mạng | — | **nack requeue** |

`message_id` không phải UUID vẫn được dùng (API ánh xạ ổn định về UUID).

### 3.2 `heartbeat`: gia hạn lease

Body: `{"worker_id", "lease_seconds": 60}` → `200 {status, lease_expires_at}`; `409 LEASE_LOST` nếu worker không còn giữ job (đã bị thu hồi). Gọi mỗi 20 giây trong lúc chuyển đổi.

### 3.3 `complete`: thành công

Body: `{"worker_id", "message_id", "result_key", "page_count", "file_size"}` (tuỳ chọn `converter_version`, `duration_ms`). Worker chỉ gọi sau khi ghi PDF vào `result_bucket/result_key` và kiểm tra PDF đọc được, ≥ 1 trang. API kiểm tra: worker và `message_id` đang giữ job; `result_key` đúng khoá đã cấp khi claim; object tồn tại, đúng `file_size`, bắt đầu bằng `%PDF-`; rồi tự tính SHA-256, ghi `ResultObject` (hết hạn sau 24 giờ) và chuyển job sang `succeeded`. `200` + job; gọi lại là idempotent. `422 RESULT_OBJECT_INVALID` / `INVALID_RESULT_KEY` nếu sai; `409 LEASE_LOST` nếu mất lease.

### 3.4 `fail`: thất bại

Body: `{"worker_id", "message_id", "error_code", "error_message", "retryable"}`. Phản hồi `200` + job, `status` là:

* `queued` nếu `retryable` và `attempts < max_attempts`. Worker **nack requeue** chính message này để thử lại; API nhả khoá khử trùng lặp của lần thử vừa lỗi nên message được claim lại.
* `failed` nếu không thử lại được hoặc hết lượt. Worker **dead-letter** message (nack không requeue) để vào hàng chờ lỗi.

Không đưa nội dung file, URL ký hay khoá vào `error_message`.

## 4. Lease, thử lại và phục hồi

| Tham số | Giá trị | Nơi cấu hình |
|---|---|---|
| Lease | 60 s | `WORKER_LEASE_SECONDS` |
| Heartbeat | 20 s | `WORKER_HEARTBEAT_INTERVAL` |
| Timeout một lần chuyển đổi | 30 s (nhỏ hơn lease) | `WORKER_CONVERT_TIMEOUT` |
| Số lần thử | 3 | `WORKER_MAX_ATTEMPTS` (worker), `MAX_ATTEMPTS` (API) — giữ hai giá trị bằng nhau |
| PDF kết quả hết hạn | 24 giờ | `RESULT_TTL_HOURS` (API) |
| URL tải PDF | 10 phút | `DOWNLOAD_URL_TTL_SECONDS` (API) |
| Chu kỳ thu hồi lease | 10 s | `REAPER_INTERVAL_SECONDS` (relay) |

* **Fencing**: `heartbeat`, `complete`, `fail` chỉ được chấp nhận khi job đang `running` dưới đúng `worker_id` (và `message_id`). Worker bị thu hồi lease nhận `409 LEASE_LOST`; kết quả của nó bị bỏ.
* **Thu hồi lease**: relay định kỳ tìm job `running` có `lease_expires_at < now()`, đóng attempt (`outcome = lease_expired`, `error_code = LEASE_EXPIRED`), rồi đưa job về `queued` kèm sự kiện outbox mới, hoặc `failed` nếu hết lượt. Cửa sổ phục hồi tối đa ≈ lease + 10 s + độ trễ relay (~70 s).
* **Worker chết**: message chưa ack được broker giao lại → `claim` trả `Duplicate message_id` → worker ack bỏ; job được cứu bằng thu hồi lease → message mới. Vì vậy cần relay đang chạy.
* **Ack/nack**: ack sau khi API đã ghi tiến triển (hoặc claim bị từ chối vì trùng/đã xong); nack requeue khi lỗi tạm thời hoặc để thử lại; dead-letter khi hết lượt, JSON hỏng hoặc thiếu `job_id`. Số lần thử đếm bằng `attempts` trong DB.

## 5. Mã lỗi

API chuẩn hoá `error_code` do worker gửi; mã lạ thành `INTERNAL_ERROR`.

| Worker gửi | Lưu trong job | Ý nghĩa | `retryable` của worker |
|---|---|---|---|
| `INVALID_INPUT` | `INVALID_FILE` | file nguồn không hợp lệ (magic bytes, quá lớn, ảnh hỏng) | false |
| `CONVERT_TIMEOUT` | `CONVERSION_TIMEOUT` | quá thời hạn chuyển đổi | true |
| `INVALID_OUTPUT_PDF` | `OUTPUT_INVALID` | PDF đầu ra không đọc được | true |
| `WORKER_ERROR`, `MISSING_SOURCE_KEY` | `INTERNAL_ERROR` | lỗi hệ thống, S3, mạng | true / false |
| — | `LEASE_EXPIRED` | do relay đặt khi thu hồi lease | — |

Các mã `UNSUPPORTED_FORMAT`, `FILE_TOO_LARGE`, `CONVERSION_ERROR` được giữ nguyên nếu worker gửi.

## 6. Object storage

| Bucket | Khoá | Ghi |
|---|---|---|
| `sources` | `jobs/{job_id}/{tên đã làm sạch}.{đuôi chuẩn}` | API; worker chỉ đọc |
| `results` | `jobs/{job_id}/attempt-{attempt_no}.pdf` | worker; người dùng chỉ tải qua URL ký ngắn hạn do API cấp |

Không bucket nào công khai.
