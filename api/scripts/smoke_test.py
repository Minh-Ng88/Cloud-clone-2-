"""Kiểm thử toàn tuyến trên hệ thống đang chạy: API -> PostgreSQL -> S3 -> relay -> RabbitMQ.

Chạy (sau `docker compose up -d --build`):
    docker compose exec api python scripts/smoke_test.py

Script tự đóng vai worker (gọi /api/internal/jobs/* đúng như worker/api_client.py) nên chạy được khi chưa
có worker thật; nếu worker thật đang chạy, hãy tắt nó trước (hai bên sẽ tranh message). Bước phục hồi lease do
relay thực hiện nên mất tối đa ~15 giây.
"""
import hashlib
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # để import app.*

from _http import API_URL, call, login, upload

FAILED: list[str] = []


def check(ok: bool, label: str, extra: str = "") -> None:
    print(f"  [{'OK ' if ok else 'LỖI'}] {label}{(' — ' + extra) if extra and not ok else ''}")
    if not ok:
        FAILED.append(label)


def minimal_pdf(text: str = "Doc2PDF smoke test") -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>"]
    stream = f"BT /F1 14 Tf 20 50 Td ({text}) Tj ET".encode()
    objs += [b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
             b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


def internal(job_id, action, body, key):
    return call("POST", f"/api/internal/jobs/{job_id}/{action}", headers={"X-Service-Key": key}, body=body)


def main() -> int:
    from app.config import get_settings
    from app.storage import get_storage
    key = get_settings().internal_service_key
    print(f"Smoke test với {API_URL}")

    print("1. Sức khoẻ hệ thống")
    status, body = call("GET", "/ready")
    check(status == 200, "GET /ready (DB + object storage)", str(body))

    print("2. Đăng nhập và phân quyền")
    alice, bob, ops = login("alice", "alice123"), login("bob", "bob123"), login("operator", "operator123")
    check(call("GET", "/jobs")[0] == 401, "không token -> 401")
    check(call("GET", "/jobs", "token-sai")[0] == 401, "token sai -> 401")
    check(call("GET", "/operator/stats", alice)[0] == 403, "user gọi /operator/stats -> 403")
    check(call("GET", "/operator/jobs", alice)[0] == 403, "user gọi /operator/jobs -> 403")
    check(call("GET", "/operator/stats", ops)[0] == 200, "operator gọi /operator/stats -> 200")

    print("3. Upload + tạo job; từ chối file lỗi")
    t0 = time.time()
    status, job = upload(alice, "xin-chao.txt", "Xin chào Việt Nam\n".encode(), {"font_size": 12})
    check(status == 202 and job.get("status") == "queued", "POST /jobs -> 202 queued", str(job))
    check(time.time() - t0 < 2, f"trả job ID dưới 2 giây ({time.time() - t0:.2f}s)")
    jid = job["id"]
    check(call("GET", f"/jobs/{jid}", alice)[1].get("id") == jid, "xem trạng thái job của mình")
    check(call("GET", f"/jobs/{jid}", bob)[0] == 404, "user khác không xem được job")
    check(upload(alice, "gia.png", b"day la van ban")[0] == 422, "file giả đuôi .png -> 422 INVALID_FILE")
    check(upload(alice, "trong.txt", b"")[0] == 400, "file rỗng -> 400")
    check(upload(alice, "a.docx", b"PK\x03\x04")[0] == 415, "định dạng không hỗ trợ -> 415")

    print("4. Relay đẩy outbox sang RabbitMQ")
    published = False
    for _ in range(30):
        _, events = call("GET", "/operator/outbox?published=true&limit=200", ops)
        if any(e["aggregate_id"] == jid for e in events):
            published = True
            break
        time.sleep(1)
    check(published, "sự kiện outbox đã được relay đánh dấu published (cần service relay đang chạy)")

    print("5. Worker giả lập (gọi /api/internal như worker thật): claim -> ghi PDF -> complete")
    check(call("POST", f"/api/internal/jobs/{jid}/claim", body={"worker_id": "w"})[0] == 401,
          "/api/internal không có service key -> 401")
    check(internal(jid, "claim", {"worker_id": "w"}, "khoa-sai")[0] == 401, "service key sai -> 401")
    mid = str(uuid.uuid4())
    status, cl = internal(jid, "claim", {"worker_id": "smoke-worker", "message_id": mid, "lease_seconds": 60}, key)
    check(status == 200 and cl.get("attempts") == 1 and cl.get("source_key", "").endswith(".txt"),
          "claim -> 200, attempts = 1, source_key có đuôi .txt", str(cl))
    status, r = internal(jid, "claim", {"worker_id": "smoke-worker", "message_id": mid}, key)
    check(status == 409 and "duplicate" in r.get("detail", "").lower(), "message giao lại bị bỏ qua (dedup)")
    status, r = internal(jid, "claim", {"worker_id": "other", "message_id": str(uuid.uuid4())}, key)
    check(status == 409, "worker thứ hai bị từ chối khi lease còn hạn")
    check(call("GET", f"/jobs/{jid}", alice)[1].get("status") == "running", "API thấy job running")
    pdf = minimal_pdf()
    get_storage().client.put_object(Bucket=cl["result_bucket"], Key=cl["result_key"], Body=pdf)
    check(internal(jid, "heartbeat", {"worker_id": "smoke-worker", "lease_seconds": 60}, key)[0] == 200,
          "heartbeat gia hạn lease")
    status, done = internal(jid, "complete", {"worker_id": "smoke-worker", "message_id": mid,
                                              "result_key": cl["result_key"], "page_count": 1,
                                              "file_size": len(pdf)}, key)
    check(status == 200 and done.get("status") == "succeeded", "complete -> succeeded", str(done))
    check((done.get("result") or {}).get("checksum") == hashlib.sha256(pdf).hexdigest(),
          "API tự tính SHA-256 của PDF kết quả")

    print("6. Tải kết quả")
    status, dl = call("GET", f"/jobs/{jid}/download", alice)
    check(status == 200 and "X-Amz-Signature" in dl.get("download_url", ""), "URL tải đã ký, có hạn")
    check(dl.get("url_expires_in") == 600, "URL sống 10 phút")
    check(dl.get("filename") == "xin-chao.pdf", "tên file tải về = xin-chao.pdf")
    check(call("GET", f"/jobs/{jid}/download", bob)[0] == 404, "user khác không tải được")

    print("7. Lỗi và thử lại")
    _, j2 = upload(alice, "hong.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 8)
    j2 = j2["id"]
    m2 = str(uuid.uuid4())
    internal(j2, "claim", {"worker_id": "smoke-worker", "message_id": m2}, key)
    status, f = internal(j2, "fail", {"worker_id": "smoke-worker", "message_id": m2, "error_code": "INVALID_INPUT",
                                      "error_message": "ảnh bị hỏng", "retryable": False}, key)
    check(status == 200 and f.get("status") == "failed" and f.get("error_code") == "INVALID_FILE",
          "lỗi không retry được -> failed, mã INVALID_INPUT chuẩn hoá thành INVALID_FILE", str(f))
    check(call("POST", f"/jobs/{j2}/retry", bob)[0] == 403, "user khác retry job người khác -> 403")
    status, r = call("POST", f"/jobs/{j2}/retry", alice)
    check(status == 202 and r.get("status") == "queued" and r.get("attempts") == 0, "chủ job retry -> queued")
    check(call("POST", f"/jobs/{jid}/retry", alice)[0] == 409, "retry job succeeded bị từ chối (409)")

    print("8. Phục hồi: worker chết, lease hết hạn, relay thu hồi (tối đa ~15 s)")
    _, j3 = upload(alice, "cham.txt", b"cham")
    j3 = j3["id"]
    internal(j3, "claim", {"worker_id": "dead-worker", "message_id": str(uuid.uuid4()), "lease_seconds": 5}, key)
    state = {}
    for _ in range(40):
        _, state = call("GET", f"/jobs/{j3}", alice)
        if state.get("status") == "queued":
            break
        time.sleep(1)
    check(state.get("status") == "queued" and state.get("error_code") == "LEASE_EXPIRED",
          "job quay lại queued với LEASE_EXPIRED", str(state.get("status")))
    _, att = call("GET", f"/jobs/{j3}/attempts", alice)
    check(att and att[0]["outcome"] == "lease_expired", "attempt 1 ghi nhận lease_expired")
    check(internal(j3, "claim", {"worker_id": "w2", "message_id": str(uuid.uuid4())}, key)[0] == 200,
          "worker khác nhận lại được job")

    print("9. Thống kê vận hành")
    status, st = call("GET", "/operator/stats", ops)
    check(status == 200 and "jobs_by_status" in st, "GET /operator/stats", str(st))

    print("\n" + ("TẤT CẢ ĐỀU ĐẠT" if not FAILED else f"CÓ {len(FAILED)} MỤC LỖI: {FAILED}"))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
