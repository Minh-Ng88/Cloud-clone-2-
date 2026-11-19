# API Doc2PDF

Base URL cục bộ: `http://localhost:8000` (Swagger tự sinh: `/docs`).

## 1. Quy ước

| Mục | Quy ước |
|---|---|
| Định dạng | JSON UTF-8; riêng `POST /jobs` là `multipart/form-data` |
| Xác thực người dùng | `Authorization: Bearer <access_token>` lấy từ `POST /auth/login` |
| Xác thực worker | Header `X-Service-Key` (biến `INTERNAL_SERVICE_KEY`, mặc định `dev-internal-key`), chỉ cho `/api/internal/*` |
| Vai trò | `user` (chỉ thấy job của mình), `operator` (thấy mọi job, thống kê), worker (chỉ `/api/internal/*`) |
| Truy vết | Gửi `X-Request-ID` (tuỳ chọn); API trả lại header này và lưu vào `job.request_id` |
| Giới hạn file | ≤ **10 MB**; đuôi `.txt .md .png .jpg .jpeg`; nội dung phải đúng định dạng (§3.2) |

Mọi lỗi có dạng `{"error": {"code", "message", "request_id", "details?"}}`; client dựa vào `code`. Các lỗi của `/api/internal/*` có thêm `detail` (chuỗi tiếng Anh) ở cấp cao nhất cho worker.

## 2. Mô hình dữ liệu

**`ConversionJob`** (`conversion_jobs`)

| Cột | Ghi chú |
|---|---|
| `id` (uuid), `owner_id` | chủ job = `sub` của token |
| `status` | `queued` · `running` · `succeeded` · `failed` |
| `source_format` | `txt` · `md` · `png` · `jpg` (từ đuôi file) |
| `original_filename`, `options` (jsonb) | `options`: `page_size` A4/LETTER, `orientation` portrait/landscape, `font_size` 8–24, `margin_mm` 5–40 |
| `attempts`, `max_attempts` | số lần worker đã nhận job **trong đợt hiện tại** / ngân sách thử (mặc định 3). Retry thủ công đặt lại `attempts = 0`; lịch sử đầy đủ ở `ConversionAttempt` |
| `worker_id`, `lease_expires_at` | worker đang giữ job và hạn lease (chỉ có khi `running`) |
| `source_checksum` | SHA-256 hex của file nguồn, bất biến |
| `converter_version` | phiên bản converter ghi khi kết thúc (mặc định `doc2pdf-converters-1` nếu worker không báo) |
| `error_code`, `error_message` | chẩn đoán lần lỗi gần nhất |
| `request_id`, `created_at`, `updated_at`, `started_at`, `finished_at` | |

**`SourceObject`** (`source_objects`, 1–1 với job): `bucket` (`sources`), `object_key` = `jobs/{job_id}/{tên đã làm sạch}.{đuôi chuẩn}` (vd `jobs/…/slow_case.txt`; worker chọn bộ chuyển đổi theo đuôi này), `content_type`, `size_bytes`, `source_checksum`.

**`ResultObject`** (`result_objects`, 1–1, chỉ có khi thành công): `bucket` (`results`), `object_key` = `jobs/{job_id}/attempt-{attempt_no}.pdf`, `size_bytes`, `result_checksum` (API tự tính từ object), `page_count`, `converter_version`, `expires_at` (24 giờ sau khi xong), `deleted_at`.

**`ConversionAttempt`** (`conversion_attempts`): mỗi lần worker nhận job một dòng: `attempt_no` (tăng liên tục suốt đời job), `message_id` (UNIQUE, khoá khử trùng lặp), `worker_id`, `outcome` (`running` · `succeeded` · `failed` · `lease_expired`), `started_at`, `lease_expires_at`, `finished_at`, `error_code`, `error_message`, `converter_version`, `duration_ms`.

**`OutboxEvent`** (`outbox_events`): `id` (= `message_id`), `aggregate_id` (= job id), `event_type`, `payload`, `created_at`, `available_at`, `published_at`, `publish_attempts`, `last_error`. Định dạng message: `docs/contract.md`.

## 3. Endpoint

| Method & Path | Quyền | Mô tả |
|---|---|---|
| `POST /auth/login` | công khai | Đăng nhập (tài khoản demo `alice/alice123`, `bob/bob123`, `operator/operator123`) |
| `GET /auth/me` | đăng nhập | `{user_id, role}` |
| `POST /jobs` | user | Upload + tạo job, trả `202` |
| `GET /jobs?status=&limit=&offset=` | user | Job **của mình**, mới nhất trước: `{items,total,limit,offset}` |
| `GET /jobs/{id}` | chủ / operator | Chi tiết và trạng thái |
| `GET /jobs/{id}/attempts` | chủ / operator | Lịch sử các lần xử lý |
| `POST /jobs/{id}/retry` | chủ / operator | Chạy lại job `failed` (đặt lại ngân sách thử), trả `202` |
| `GET /jobs/{id}/download` | chủ / operator | URL tải PDF có hạn |
| `GET /operator/jobs` | operator | Mọi job, lọc `status`, `owner_id` |
| `GET /operator/stats` | operator | Số job theo trạng thái, tuổi hàng đợi, lease quá hạn, outbox chưa gửi |
| `GET /operator/outbox` | operator | Sự kiện outbox (`published=false` mặc định) |
| `POST /api/internal/jobs/{id}/claim` · `heartbeat` · `complete` · `fail` | worker | Giao thức worker, xem `docs/contract.md` §3 |

### 3.1 Đối tượng Job

```json
{ "id": "6e063bbd-…", "owner_id": "alice", "status": "succeeded", "source_format": "txt",
  "original_filename": "bao-cao.txt", "options": {"page_size":"A4","orientation":"portrait","font_size":11,"margin_mm":20},
  "attempts": 1, "max_attempts": 3, "worker_id": "worker-host-1a2b3c", "lease_expires_at": null,
  "source_checksum": "9f86d081…", "converter_version": "doc2pdf-converters-1", "error_code": null, "error_message": null,
  "request_id": "ca2985cb…", "created_at": "…", "updated_at": "…", "started_at": "…", "finished_at": "…",
  "source": {"size_bytes": 1832, "content_type": "text/plain"},
  "result": {"size_bytes": 20211, "page_count": 2, "checksum": "ab12…", "expires_at": "…", "expired": false},
  "download_available": true }
```
`result` là `null` khi chưa thành công; `download_available` = `succeeded` và PDF chưa hết hạn.

### 3.2 `POST /jobs`

`multipart/form-data`: `file` (bắt buộc), `options` (chuỗi JSON, tuỳ chọn). Trong **một giao dịch** API ghi job `queued` (`attempts = 0`), `SourceObject` và sự kiện outbox; file nguồn được ghi vào bucket riêng tư trước đó. Trả `202`, header `Location: /jobs/{id}`; chuyển đổi chạy bất đồng bộ.

API kiểm tra file thật (không tin đuôi hay `Content-Type`); file không đạt **không tạo job**:

| Tình huống | Phản hồi |
|---|---|
| File rỗng | `400 EMPTY_FILE` |
| Đuôi không hỗ trợ (vd `.docx`) | `415 UNSUPPORTED_FORMAT` |
| > 10 MB | `413 FILE_TOO_LARGE` |
| PNG không bắt đầu bằng `89 50 4E 47 0D 0A 1A 0A`; JPEG không bắt đầu bằng `FF D8 FF`; TXT/MD có byte NUL hoặc không phải UTF-8 | `422 INVALID_FILE` |
| `options` sai | `422 INVALID_OPTIONS` |

Header đúng nhưng thân file hỏng (vd JPEG bị cắt ngang) vẫn được nhận; worker sẽ báo lỗi và job `failed` kèm chẩn đoán.

### 3.3 Retry và tải PDF

* `POST /jobs/{id}/retry`: chỉ job `failed` (`409 INVALID_STATE` nếu khác). Đặt `status = queued`, `attempts = 0`, `max_attempts = 3`, xoá lỗi và ghi sự kiện outbox mới. Người dùng thường retry job của người khác nhận `403`.
* `GET /jobs/{id}/download` → `{download_url, url_expires_in, result_expires_at, filename}`. `download_url` là URL S3 ký sẵn sống 10 phút; quyền được kiểm tra mỗi lần gọi. `409 RESULT_NOT_READY` nếu chưa có PDF, `410 RESULT_EXPIRED` nếu quá `result_expires_at`.

### 3.4 Quyền truy cập

| Tình huống | Phản hồi |
|---|---|
| Không token / token sai / hết hạn | `401 UNAUTHENTICATED` |
| Xem hoặc tải job của người khác | `404 JOB_NOT_FOUND` (không lộ sự tồn tại của job) |
| Retry job của người khác | `403 FORBIDDEN` |
| `user` gọi `/operator/*` | `403 FORBIDDEN` |
| `/api/internal/*` không có hoặc sai `X-Service-Key` (kể cả dùng token operator) | `401 UNAUTHENTICATED` |

## 4. Mã lỗi `error_code` của job

`INVALID_FILE`, `UNSUPPORTED_FORMAT`, `FILE_TOO_LARGE`, `CONVERSION_TIMEOUT`, `CONVERSION_ERROR`, `OUTPUT_INVALID`, `LEASE_EXPIRED` (do hệ thống đặt khi thu hồi lease), `INTERNAL_ERROR`. Mã worker gửi lên được chuẩn hoá về bộ này (`docs/contract.md` §5).
