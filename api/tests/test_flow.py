"""Kiểm thử luồng chính. Cần PostgreSQL (conftest tự tạo DB `<tên DB>_test` và chạy migration).

Worker được giả lập đúng như worker/api_client.py: gọi /api/internal/jobs/* với X-Service-Key.
"""
import hashlib
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

import app.storage as storage_module
from app.db import SessionLocal, engine
from app.main import app
from app.models import OutboxEvent

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PDF = b"%PDF-1.4\n%fake-but-pdf-header\n"
SVC = {"X-Service-Key": "dev-internal-key"}


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def ensure_buckets(self): ...
    def ping(self): return True
    def put_source(self, key, data, ct): self.objects[("sources", key)] = data
    def delete(self, bucket, key): self.objects.pop((bucket, key), None)

    def inspect_object(self, bucket, key):
        o = self.objects.get((bucket, key))
        return None if o is None else {"size": len(o), "sha256": hashlib.sha256(o).hexdigest(),
                                       "is_pdf": o.startswith(b"%PDF-")}

    def presign_download(self, bucket, key, filename, ttl): return f"http://s3/{bucket}/{key}?ttl={ttl}"


@pytest.fixture()
def env(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(storage_module, "_storage", fake)
    with TestClient(app) as client:
        yield client, fake


def login(c, name, pw):
    r = c.post("/auth/login", json={"username": name, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def upload(c, h, name="a.txt", data=b"xin chao"):
    r = c.post("/jobs", headers=h, files={"file": (name, data, "text/plain")},
               data={"options": json.dumps({"font_size": 12})})
    assert r.status_code == 202, r.text
    return r.json()


def internal(c, jid, action, body, headers=SVC, prefix="/api/internal"):
    return c.post(f"{prefix}/jobs/{jid}/{action}", headers=headers, json=body)


def claim(c, jid, worker="w1", mid=None, lease=60):
    return internal(c, jid, "claim", {"worker_id": worker, "message_id": mid or str(uuid.uuid4()),
                                      "lease_seconds": lease})


def fail(c, jid, worker, mid, code, retryable, msg="x"):
    return internal(c, jid, "fail", {"worker_id": worker, "message_id": mid, "error_code": code,
                                     "error_message": msg, "retryable": retryable})


def detail(r):
    return r.json().get("detail", "")


def test_upload_status_permissions_and_download(env):
    c, fake = env
    alice, bob, ops = login(c, "alice", "alice123"), login(c, "bob", "bob123"), login(c, "operator", "operator123")
    job = upload(c, alice, "Báo cáo.txt")
    jid = job["id"]
    assert job["status"] == "queued" and job["attempts"] == 0
    assert job["source_checksum"] == hashlib.sha256(b"xin chao").hexdigest()

    assert c.get(f"/jobs/{jid}", headers=bob).status_code == 404
    assert c.get(f"/jobs/{jid}", headers=ops).status_code == 200
    assert c.get("/jobs").status_code == 401
    assert c.get("/operator/stats", headers=alice).status_code == 403
    assert c.get(f"/jobs/{jid}/download", headers=alice).status_code == 409  # chưa có PDF

    # Outbox: job và sự kiện cùng giao dịch; message {job_id, source_key, format}
    with SessionLocal() as db:
        ev = db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == uuid.UUID(jid))).one()
    assert ev.payload["job_id"] == jid and ev.payload["format"] == "txt"
    key = ev.payload["source_key"]
    assert key.startswith(f"jobs/{jid}/") and key.endswith(".txt")  # worker chọn converter theo đuôi
    assert ("sources", key) in fake.objects

    # Worker: claim -> heartbeat -> upload PDF -> complete
    mid = str(uuid.uuid4())
    r = claim(c, jid, "w1", mid)
    assert r.status_code == 200, r.text
    cl = r.json()
    assert cl["status"] == "running" and cl["attempts"] == 1 and cl["source_key"] == key
    assert cl["result_key"] == f"jobs/{jid}/attempt-1.pdf"
    r = claim(c, jid, "w1", mid)
    assert r.status_code == 409 and "duplicate" in detail(r).lower()          # worker bỏ qua + ack
    r = claim(c, jid, "w2")
    assert r.status_code == 409 and not any(w in detail(r).lower() for w in ("already", "succeeded", "duplicate"))
    assert c.get(f"/jobs/{jid}", headers=alice).json()["status"] == "running"
    assert internal(c, jid, "heartbeat", {"worker_id": "w1", "lease_seconds": 60}).status_code == 200
    assert internal(c, jid, "heartbeat", {"worker_id": "w2", "lease_seconds": 60}).status_code == 409

    done = {"worker_id": "w1", "message_id": mid, "result_key": cl["result_key"], "page_count": 1,
            "file_size": len(PDF)}
    r = internal(c, jid, "complete", done)
    assert r.status_code == 422 and r.json()["error"]["code"] == "RESULT_OBJECT_INVALID"  # chưa có object
    fake.objects[("results", cl["result_key"])] = PDF
    assert internal(c, jid, "complete", {**done, "result_key": "x.pdf"}).status_code == 422  # sai khoá
    assert internal(c, jid, "complete", {**done, "worker_id": "w2"}).status_code == 409      # sai worker
    r = internal(c, jid, "complete", done)
    assert r.status_code == 200 and r.json()["status"] == "succeeded"
    assert internal(c, jid, "complete", done).status_code == 200                              # idempotent

    j = c.get(f"/jobs/{jid}", headers=alice).json()
    assert j["status"] == "succeeded" and j["download_available"]
    assert j["result"]["checksum"] == hashlib.sha256(PDF).hexdigest() and j["result"]["page_count"] == 1
    d = c.get(f"/jobs/{jid}/download", headers=alice)
    assert d.status_code == 200 and d.json()["url_expires_in"] == 600 and d.json()["filename"].endswith(".pdf")
    assert c.get(f"/jobs/{jid}/download", headers=bob).status_code == 404
    r = claim(c, jid, "w3")
    assert r.status_code == 409 and "already" in detail(r).lower()                            # worker ack
    assert [a["outcome"] for a in c.get(f"/jobs/{jid}/attempts", headers=alice).json()] == ["succeeded"]


def test_internal_endpoints_are_service_only(env):
    c, _ = env
    alice, ops = login(c, "alice", "alice123"), login(c, "operator", "operator123")
    jid = upload(c, alice)["id"]
    body = {"worker_id": "w", "message_id": str(uuid.uuid4())}
    assert c.post(f"/api/internal/jobs/{jid}/claim", json=body).status_code == 401                    # không khoá
    assert internal(c, jid, "claim", body, headers={"X-Service-Key": "sai"}).status_code == 401       # sai khoá
    assert internal(c, jid, "claim", body, headers=alice).status_code == 401                          # token người dùng
    assert internal(c, jid, "claim", body, headers=ops).status_code == 401                            # kể cả operator
    assert internal(c, uuid.uuid4(), "claim", body).status_code == 404                                # job không tồn tại
    assert claim(c, jid, "w1").status_code == 200                                                     # khoá đúng
    assert c.post(f"/internal/jobs/{jid}/heartbeat", headers=SVC, json={"worker_id": "w1"}).status_code == 200  # bí danh


def test_upload_rules_match_reference_dataset(env):
    """Khớp data/expected.csv: 5 file `rejected` bị từ chối ngay khi upload, `corrupt.jpg` được nhận."""
    c, _ = env
    h = login(c, "alice", "alice123")
    post = lambda name, data: c.post("/jobs", headers=h, files={"file": (name, data)})  # noqa: E731
    code = lambda r: r.json()["error"]["code"]  # noqa: E731
    r = post("empty.txt", b"");                                   assert (r.status_code, code(r)) == (400, "EMPTY_FILE")
    r = post("fake_image.png", b"Day la van ban, khong phai anh"); assert (r.status_code, code(r)) == (422, "INVALID_FILE")
    r = post("exe_renamed.png", b"MZ\x90\x00\x03\x00" + b"\x01" * 64); assert (r.status_code, code(r)) == (422, "INVALID_FILE")
    r = post("unsupported.docx", b"PK\x03\x04" + bytes(64));       assert (r.status_code, code(r)) == (415, "UNSUPPORTED_FORMAT")
    r = post("toolarge.txt", b"A" * (10 * 1024 * 1024 + 1));      assert (r.status_code, code(r)) == (413, "FILE_TOO_LARGE")
    assert code(post("binary.txt", b"abc\x00def")) == "INVALID_FILE"
    assert post("corrupt.jpg", b"\xff\xd8\xff\xe0" + b"garbage" * 50).status_code == 202
    assert post("ok.png", PNG).status_code == 202
    assert c.post("/jobs", headers=h, files={"file": ("a.txt", b"x")}, data={"options": "{bad"}).status_code == 422
    assert c.post("/jobs", files={"file": ("a.txt", b"x")}).status_code == 401


def test_source_key_keeps_name_and_extension(env):
    c, fake = env
    h = login(c, "alice", "alice123")
    for name, expect in [("slow_case.txt", "slow_case.txt"), ("Ảnh đẹp.JPEG", "nh_p.jpg"), ("ghi chú.markdown", "ghi_ch.md")]:
        data = b"\xff\xd8\xff\xe0" + b"0" * 8 if name.upper().endswith("JPEG") else b"noi dung"
        j = upload(c, h, name, data)
        with SessionLocal() as db:
            ev = db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == uuid.UUID(j["id"]))).one()
        base = os.path.basename(ev.payload["source_key"])
        assert base.endswith(os.path.splitext(expect)[1]), base
        if name == "slow_case.txt":
            assert base == "slow_case.txt"  # worker nhận diện file demo chậm theo tên


def test_worker_retry_flow_and_error_code_mapping(env):
    c, _ = env
    h = login(c, "alice", "alice123")
    jid = upload(c, h)["id"]
    mid = str(uuid.uuid4())  # worker nack-requeue CÙNG message để thử lại
    for n in (1, 2, 3):
        r = claim(c, jid, "w1", mid)
        assert r.status_code == 200, (n, r.text)
        assert r.json()["attempts"] == n
        retryable = n < 3  # worker tự tính can_retry = retryable and attempts < 3
        r = fail(c, jid, "w1", mid, "CONVERT_TIMEOUT", retryable)
        assert r.status_code == 200 and r.json()["status"] == ("queued" if retryable else "failed")
    j = c.get(f"/jobs/{jid}", headers=h).json()
    assert j["status"] == "failed" and j["error_code"] == "CONVERSION_TIMEOUT" and j["attempts"] == 3
    assert fail(c, jid, "w1", mid, "WORKER_ERROR", False).status_code == 409                   # worker cũ không ghi nữa
    with SessionLocal() as db:  # retry tự động KHÔNG ghi thêm outbox (worker nack-requeue message cũ)
        assert len(db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == uuid.UUID(jid))).all()) == 1

    bob = login(c, "bob", "bob123")
    assert c.post(f"/jobs/{jid}/retry", headers=bob).status_code == 403                        # S5
    r = c.post(f"/jobs/{jid}/retry", headers=h)                                                # retry thủ công
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert r.json()["attempts"] == 0 and r.json()["max_attempts"] == 3                        # ngân sách mới
    assert c.post(f"/jobs/{jid}/retry", headers=h).status_code == 409
    with SessionLocal() as db:  # retry thủ công ghi outbox mới
        assert len(db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == uuid.UUID(jid))).all()) == 2
    r = claim(c, jid, "w1", str(uuid.uuid4()))
    assert r.status_code == 200 and r.json()["attempts"] == 1 and r.json()["attempt_no"] == 4  # lịch sử nối tiếp
    assert r.json()["result_key"] == f"jobs/{jid}/attempt-4.pdf"

    for worker_code, canon in [("INVALID_INPUT", "INVALID_FILE"), ("INVALID_OUTPUT_PDF", "OUTPUT_INVALID"),
                               ("WORKER_ERROR", "INTERNAL_ERROR"), ("LA_HOAC", "INTERNAL_ERROR")]:
        j2 = upload(c, h)["id"]
        m2 = str(uuid.uuid4())
        assert claim(c, j2, "w1", m2).status_code == 200
        assert fail(c, j2, "w1", m2, worker_code, False).status_code == 200
        assert c.get(f"/jobs/{j2}", headers=h).json()["error_code"] == canon


def test_non_uuid_message_id_is_deduplicated(env):
    c, _ = env
    h = login(c, "alice", "alice123")
    jid = upload(c, h)["id"]
    assert claim(c, jid, "w1", "msg-dedup-001").status_code == 200
    r = claim(c, jid, "w2", "msg-dedup-001")
    assert r.status_code == 409 and "duplicate" in detail(r).lower()


def test_lease_expiry_is_reclaimed_by_relay(env):
    c, _ = env
    h = login(c, "alice", "alice123")
    ops = login(c, "operator", "operator123")
    jid = upload(c, h)["id"]
    assert claim(c, jid, "dead-worker").status_code == 200
    with engine.begin() as conn:  # giả lập worker chết: lease đã hết hạn
        conn.execute(text("UPDATE conversion_jobs SET lease_expires_at = now() - interval '1 second' "
                          "WHERE id = CAST(:j AS uuid)"), {"j": jid})
    assert c.get("/operator/stats", headers=ops).json()["running_with_expired_lease"] >= 1
    reaper = os.path.join(os.path.dirname(__file__), "..", "..", "relay", "reaper.py")
    if not os.path.exists(reaper):
        pytest.skip("relay/ không có trong image test; smoke_test kiểm bước này")
    import importlib.util
    spec = importlib.util.spec_from_file_location("relay_reaper", reaper)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.reclaim_expired(engine)[0] >= 1           # chính hàm relay chạy định kỳ
    j = c.get(f"/jobs/{jid}", headers=h).json()
    assert j["status"] == "queued" and j["error_code"] == "LEASE_EXPIRED"
    assert c.get(f"/jobs/{jid}/attempts", headers=h).json()[0]["outcome"] == "lease_expired"
    with SessionLocal() as db:  # có message mới để worker nhận lại
        assert len(db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == uuid.UUID(jid))).all()) == 2
    r = claim(c, jid, "w2", str(uuid.uuid4()))
    assert r.status_code == 200 and r.json()["attempt_no"] == 2
