#!/usr/bin/env python3
"""Seed script Doc2PDF: tao du lieu mau (user gia, job mau) qua API, chay lai nhieu lan cho cung ket qua.

Chi dung thu vien chuan cua Python (khong can pip install). Chay tu thu muc goc repo:

    python data/seed.py                  # tao job mau tu bo file test cua nhom
    python data/seed.py --verify         # tao xong thi cho job chay xong va doi chieu data/expected.csv
    python data/seed.py --dry-run        # chi in ke hoach, khong goi API

Cach hoat dong:
  * Doc data/expected.csv (danh sach file test + ket qua mong doi) cua Muoi.
  * Moi file duoc gan co dinh cho mot tai khoan demo (alice hoac bob) theo hash cua ten file,
    nen chay bao nhieu lan, o may nao cung ra cung mot phan chia.
  * API chua co khoa chong trung (idempotency) khi tao job, nen script tu kiem tra truoc khi
    upload: neu tai khoan do da co job cung ten file va cung SHA-256 thi bo qua, khong tao them.
  * File bi API tu choi (rong, sai duoi, qua 10 MB) khong tao job, chay lai se bi tu choi y het.
  * Tai khoan demo la cac tai khoan gia (synthetic) cau hinh trong API (DEMO_ACCOUNTS).

Ma thoat: 0 = on, 1 = co file khong dung mong doi (hoac thieu file), 2 = khong ket noi/dang nhap duoc.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024
TERMINAL = {"succeeded", "failed"}
REJECT_CODES = {"INVALID_FILE", "UNSUPPORTED_FORMAT", "FILE_TOO_LARGE", "EMPTY_FILE"}
CONTENT_TYPES = {".txt": "text/plain", ".md": "text/markdown", ".png": "image/png",
                 ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
SLOW_CASE = "invalid/slow_case.txt"  # chi co y nghia khi worker bat che do chuyen doi cham
DEFAULT_ACCOUNTS = "alice:alice123,bob:bob123"  # trung DEMO_ACCOUNTS mac dinh cua API (chi de dev)


class SeedError(Exception):
    """Loi khong the tiep tuc (ket noi, dang nhap...)."""


@dataclass
class Case:
    filename: str  # vd "valid/txt_short.txt"
    ftype: str
    expected: str  # succeeded | rejected | failed
    reason: str
    min_pages: int
    owner: str = ""
    data: bytes | None = None
    note: str = ""
    # ket qua
    kind: str = "pending"  # created | existing | rejected | skipped | missing | error
    job: dict | None = None
    http: int | None = None
    code: str = ""
    message: str = ""
    ok: bool | None = None
    actual: str = ""

    @property
    def base(self) -> str:
        return os.path.basename(self.filename)


# ----------------------------------------------------------------------------- dau vao

def parse_accounts(text: str) -> dict[str, str]:
    """'alice:alice123,bob:bob123' -> {'alice': 'alice123', 'bob': 'bob123'} (giu thu tu)."""
    out: dict[str, str] = {}
    for item in text.split(","):
        parts = item.strip().split(":")
        if len(parts) >= 2 and parts[0]:
            out[parts[0]] = parts[1]
    if not out:
        raise SeedError("SEED_ACCOUNTS rong hoac sai dinh dang (dung 'user:matkhau,user2:matkhau2').")
    return out


def owner_for(filename: str, users: list[str]) -> str:
    """Gan co dinh theo ten file: khong phu thuoc thu tu, thoi gian hay lan chay."""
    h = int(hashlib.sha256(filename.encode("utf-8")).hexdigest(), 16)
    return users[h % len(users)]


def load_cases(data_dir: Path, users: list[str], include_slow: bool) -> list[Case]:
    csv_path = data_dir / "expected.csv"
    if not csv_path.exists():
        raise SeedError(f"Khong thay {csv_path}. Chay `python data/generate.py` hoac kiem tra --data-dir.")
    cases: list[Case] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            c = Case(row["filename"].strip(), row["type"].strip(), row["expected_status"].strip(),
                     row["expected_reason"].strip(), int(row["expected_min_pages"] or 0))
            c.owner = owner_for(c.filename, users)
            cases.append(c)
    for c in cases:
        if c.filename == SLOW_CASE and not include_slow:
            c.kind, c.note = "skipped", "can worker o che do cham, dung --include-slow"
            continue
        path = data_dir / c.filename
        if path.exists():
            c.data = path.read_bytes()
        elif c.filename == "invalid/toolarge.txt":
            # File nay nam trong .gitignore (>10 MB) nen khong co trong repo: tao tai cho, khong ghi ra dia.
            c.data, c.note = b"A" * (MAX_BYTES + 1), "tao tai cho (>10 MB)"
        else:
            c.kind, c.note = "missing", "thieu file, chay `python data/generate.py`"
    return cases


# ----------------------------------------------------------------------------- HTTP

def call(method: str, url: str, token: str | None = None, body: bytes | None = None,
         content_type: str | None = None, timeout: float = 60) -> tuple[int, dict | None]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise SeedError(f"Khong ket noi duoc {url}: {getattr(e, 'reason', e)}") from e
    try:
        return status, json.loads(raw.decode("utf-8")) if raw else None
    except ValueError:
        return status, None


def multipart(filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = "----seed" + uuid.uuid4().hex
    ctype = CONTENT_TYPES.get(os.path.splitext(filename)[1].lower()) or mimetypes.guess_type(filename)[0] \
        or "application/octet-stream"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode("utf-8")
    return head + data + f"\r\n--{boundary}--\r\n".encode("utf-8"), f"multipart/form-data; boundary={boundary}"


def error_of(payload: dict | None) -> tuple[str, str]:
    e = (payload or {}).get("error") or {}
    return str(e.get("code", "")), str(e.get("message", ""))


def login(api: str, user: str, password: str) -> str:
    status, p = call("POST", f"{api}/auth/login", body=json.dumps({"username": user, "password": password}).encode(),
                     content_type="application/json", timeout=15)
    if status != 200 or not p or "access_token" not in p:
        raise SeedError(f"Dang nhap '{user}' that bai (HTTP {status}). Kiem tra SEED_ACCOUNTS / DEMO_ACCOUNTS cua API.")
    return p["access_token"]


def list_jobs(api: str, token: str) -> list[dict]:
    """Lay het job cua tai khoan (phan trang 100/trang)."""
    items, offset = [], 0
    while True:
        status, p = call("GET", f"{api}/jobs?limit=100&offset={offset}", token, timeout=30)
        if status != 200 or p is None:
            raise SeedError(f"GET /jobs loi (HTTP {status}).")
        items += p["items"]
        offset += 100
        if offset >= p["total"] or not p["items"]:
            return items


# ----------------------------------------------------------------------------- seed

def seed(api: str, cases: list[Case], accounts: dict[str, str]) -> dict[str, str]:
    status, _ = call("GET", f"{api}/health", timeout=10)
    if status != 200:
        raise SeedError(f"{api}/health tra HTTP {status}. API da chay chua? (docker compose up -d --build)")
    users = sorted({c.owner for c in cases if c.kind == "pending"})
    tokens = {u: login(api, u, accounts[u]) for u in users}
    existing = {u: list_jobs(api, tokens[u]) for u in users}  # danh sach moi nhat truoc

    for c in cases:
        if c.kind != "pending":
            continue
        checksum = hashlib.sha256(c.data).hexdigest()
        found = next((j for j in existing[c.owner]
                      if j["original_filename"] == c.base and j.get("source_checksum") == checksum), None)
        if found:
            c.kind, c.job = "existing", found
            continue
        body, ctype = multipart(c.base, c.data)
        status, p = call("POST", f"{api}/jobs", tokens[c.owner], body, ctype, timeout=120)
        if status == 202 and p:
            c.kind, c.job = "created", p
        elif 400 <= status < 500:
            c.kind, c.http = "rejected", status
            c.code, c.message = error_of(p)
        else:
            c.kind, c.http = "error", status
            c.code, c.message = error_of(p)
    return tokens


def wait_for_jobs(api: str, cases: list[Case], tokens: dict[str, str], timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while True:
        pending = [c for c in cases if c.job and c.job["status"] not in TERMINAL]
        if not pending or time.monotonic() > deadline:
            return
        for c in pending:
            status, p = call("GET", f"{api}/jobs/{c.job['id']}", tokens[c.owner], timeout=30)
            if status == 200 and p:
                c.job = p
        time.sleep(2)


# ----------------------------------------------------------------------------- doi chieu

def evaluate(c: Case) -> None:
    """Dat c.ok va c.actual theo bang expected.csv. (rejected = API tu choi HOAC worker tu choi vi file sai.)"""
    if c.kind == "skipped":
        c.ok, c.actual = None, f"bo qua: {c.note}"
    elif c.kind == "missing":
        c.ok, c.actual = False, f"THIEU FILE: {c.note}"
    elif c.kind == "error":
        c.ok, c.actual = False, f"loi may chu HTTP {c.http} {c.code}".strip()
    elif c.kind == "rejected":
        c.ok, c.actual = c.expected == "rejected", f"API tu choi {c.http} {c.code}".strip()
    else:
        j = c.job or {}
        st, code = j.get("status", "?"), j.get("error_code") or ""
        pages = (j.get("result") or {}).get("page_count")
        if st not in TERMINAL:
            c.ok, c.actual = False, f"chua xong sau khi cho ({st})"
        elif st == "succeeded":
            c.actual = f"succeeded, {pages} trang"
            c.ok = c.expected == "succeeded" and pages is not None and pages >= c.min_pages
        else:
            c.actual = f"failed {code}".strip()
            if c.expected == "rejected":
                c.ok = code in REJECT_CODES
                c.actual = f"worker tu choi ({code})"
            elif c.expected == "failed":
                c.ok = code == "CONVERSION_TIMEOUT" if "timeout" in c.reason else True
            else:
                c.ok = False


# ----------------------------------------------------------------------------- in an

def table(rows: list[list[str]], head: list[str]) -> str:
    w = [max(len(str(r[i])) for r in [head] + rows) for i in range(len(head))]
    line = lambda r: "  ".join(str(v).ljust(w[i]) for i, v in enumerate(r)).rstrip()
    return "\n".join([line(head), "  ".join("-" * x for x in w)] + [line(r) for r in rows])


def print_plan(cases: list[Case]) -> None:
    rows = [[c.filename, c.owner, c.expected, str(len(c.data)) if c.data is not None else "-",
             hashlib.sha256(c.data).hexdigest()[:10] if c.data is not None else "-",
             c.note if c.kind != "pending" else ""] for c in cases]
    print(table(rows, ["file", "tai khoan", "mong doi", "bytes", "sha256", "ghi chu"]))


def print_seed_result(cases: list[Case]) -> None:
    label = {"created": "tao moi", "existing": "da co", "rejected": "API tu choi", "skipped": "bo qua",
             "missing": "THIEU FILE", "error": "LOI"}
    rows = []
    for c in cases:
        detail = (c.job or {}).get("id", "")[:8] if c.job else (f"{c.http} {c.code}".strip() if c.http else c.note)
        rows.append([label[c.kind], c.filename, c.owner, detail])
    print(table(rows, ["ket qua", "file", "tai khoan", "chi tiet"]))
    n = lambda k: sum(1 for c in cases if c.kind == k)
    print(f"\nTong: {len(cases)} file | tao moi {n('created')} | da co {n('existing')} | "
          f"API tu choi {n('rejected')} | bo qua {n('skipped')} | thieu {n('missing')} | loi {n('error')}")


def print_verify(cases: list[Case]) -> int:
    for c in cases:
        evaluate(c)
    rows = [[c.filename, c.owner, c.expected, c.actual, {True: "DAT", False: "KHONG DAT", None: "-"}[c.ok]] for c in cases]
    print(table(rows, ["file", "tai khoan", "mong doi", "thuc te", "ket qua"]))
    bad = [c for c in cases if c.ok is False]
    ok = sum(1 for c in cases if c.ok)
    print(f"\nDoi chieu expected.csv: {ok} dat, {len(bad)} khong dat, {sum(1 for c in cases if c.ok is None)} bo qua")
    return 1 if bad else 0


def write_report(path: Path, api: str, cases: list[Case]) -> None:
    for c in cases:
        if c.ok is None and c.kind != "skipped":
            evaluate(c)
    doc = {"generated_at": datetime.now(timezone.utc).isoformat(), "api": api,
           "cases": [{"file": c.filename, "owner": c.owner, "expected": c.expected, "kind": c.kind,
                      "http": c.http, "code": c.code, "job_id": (c.job or {}).get("id"),
                      "job_status": (c.job or {}).get("status"), "error_code": (c.job or {}).get("error_code"),
                      "actual": c.actual, "ok": c.ok} for c in cases]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Da ghi bao cao: {path}")


# ----------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # console Windows cu khong in duoc Unicode thi thay bang '?'
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Seed du lieu mau Doc2PDF qua API (chay lai duoc, khong tao trung).")
    ap.add_argument("--api-url", default=os.environ.get("SEED_API_URL", "http://localhost:8000"))
    ap.add_argument("--data-dir", type=Path, default=here, help="thu muc co expected.csv, valid/, invalid/")
    ap.add_argument("--dry-run", action="store_true", help="chi in ke hoach, khong goi API")
    ap.add_argument("--verify", action="store_true", help="cho job chay xong roi doi chieu expected.csv")
    ap.add_argument("--timeout", type=int, default=120, help="so giay toi da cho job chay xong (mac dinh 120)")
    ap.add_argument("--include-slow", action="store_true", help="gui ca slow_case.txt (can worker o che do cham)")
    ap.add_argument("--report", type=Path, help="ghi ket qua ra file JSON (vd docs/evidence/seed.json)")
    args = ap.parse_args(argv)
    api = args.api_url.rstrip("/")

    try:
        accounts = parse_accounts(os.environ.get("SEED_ACCOUNTS", DEFAULT_ACCOUNTS))
        cases = load_cases(args.data_dir, list(accounts), args.include_slow)
        if args.dry_run:
            print_plan(cases)
            return 1 if any(c.kind == "missing" for c in cases) else 0
        print(f"API: {api}")
        tokens = seed(api, cases, accounts)
        print_seed_result(cases)
        code = 1 if any(c.kind in ("missing", "error") or (c.kind == "rejected" and c.expected == "succeeded")
                        for c in cases) else 0
        if args.verify:
            print(f"\nDang cho job chay xong (toi da {args.timeout}s)...")
            wait_for_jobs(api, cases, tokens, args.timeout)
            print()
            code = max(code, print_verify(cases))
        if args.report:
            write_report(args.report, api, cases)
        return code
    except SeedError as e:
        print(f"LOI: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
