"""Kiểm thử tích hợp (Test 2 tự động): gửi 27 file qua API, so với data/expected.csv.

Chạy (từ thư mục gốc repo, API đang chạy):
    pip install requests pypdf
    set API_URL=http://localhost:8000          (PowerShell: $env:API_URL="http://localhost:8000")
    set API_TOKEN=<token của user test>         (bỏ trống nếu API chưa bắt đăng nhập)
    python tests/run_integration_test.py

Kết quả: bảng đạt / không đạt in ra màn hình, ghi tests/integration_results.csv,
thoát với mã 1 nếu có file không đạt.

!!! ĐỐI CHIẾU PHẦN CẤU HÌNH BÊN DƯỚI VỚI docs/api.md CỦA KHÔI trước khi chạy !!!
"""
import csv
import io
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# ----------------------------- CẤU HÌNH (sửa cho khớp docs/api.md) -----------------------------
API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
API_TOKEN = os.environ.get("API_TOKEN", "")
UPLOAD_PATH = "/jobs"                       # POST multipart, trường file tên "file"
UPLOAD_FIELD = "file"
STATUS_PATH = "/jobs/{job_id}"              # GET -> JSON có trường status
DOWNLOAD_PATH = "/jobs/{job_id}/download"   # GET -> byte PDF, hoặc JSON có trường url
JOB_ID_KEY = "job_id"
STATUS_KEY = "status"
REASON_KEY = "error"                        # trường lý do khi failed (nếu có)
TERMINAL = {"succeeded", "failed"}
REJECT_CODES = {400, 413, 415, 422}         # mã HTTP coi là "rejected" ngay lúc upload
POLL_SECONDS = 1.0
TIMEOUT_SECONDS = 120                       # chờ tối đa mỗi job (cho slow_case có thể cần dài hơn)
# --------------------------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HEADERS = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}


def get_pdf_bytes(job_id: str) -> bytes | None:
    resp = requests.get(API_URL + DOWNLOAD_PATH.format(job_id=job_id), headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        return None
    if resp.headers.get("content-type", "").startswith("application/json"):
        url = resp.json().get("url")
        if not url:
            return None
        resp = requests.get(url, timeout=30)  # signed URL: không ghi vào log
        if resp.status_code != 200:
            return None
    return resp.content


def check_pdf(data: bytes, min_pages: int) -> tuple[bool, str]:
    """PDF hợp lệ: bắt đầu bằng %PDF, mở được và đủ số trang tối thiểu."""
    if not data.startswith(b"%PDF"):
        return False, "không phải PDF (thiếu %PDF)"
    try:
        from pypdf import PdfReader
        pages = len(PdfReader(io.BytesIO(data)).pages)
    except Exception as exc:  # PDF hỏng
        return False, f"PDF không mở được: {type(exc).__name__}"
    if pages < min_pages:
        return False, f"{pages} trang < {min_pages} tối thiểu"
    return True, f"{pages} trang"


def run_one(row: dict) -> dict:
    path = DATA / row["filename"]
    result = {"filename": row["filename"], "expected": row["expected_status"], "actual": "", "job_id": "",
              "seconds": 0.0, "passed": False, "note": ""}
    t0 = time.perf_counter()
    try:
        with path.open("rb") as fh:
            resp = requests.post(API_URL + UPLOAD_PATH, headers=HEADERS, timeout=60,
                                 files={UPLOAD_FIELD: (path.name, fh)})
        if resp.status_code in REJECT_CODES:
            result["actual"] = "rejected"
            result["note"] = f"HTTP {resp.status_code}"
        elif resp.status_code >= 400:
            result["actual"] = "error"
            result["note"] = f"HTTP {resp.status_code}"
        else:
            job_id = str(resp.json()[JOB_ID_KEY])
            result["job_id"] = job_id
            deadline = time.monotonic() + TIMEOUT_SECONDS
            status = ""
            while time.monotonic() < deadline:
                body = requests.get(API_URL + STATUS_PATH.format(job_id=job_id), headers=HEADERS, timeout=30).json()
                status = str(body.get(STATUS_KEY, ""))
                if status in TERMINAL or status == "rejected":
                    result["note"] = str(body.get(REASON_KEY, "") or "")
                    break
                time.sleep(POLL_SECONDS)
            else:
                status = f"kẹt ở {status or 'không rõ'} sau {TIMEOUT_SECONDS}s"
            result["actual"] = status
            if status == "succeeded" and row["expected_status"] == "succeeded":
                ok_pdf, info = check_pdf(get_pdf_bytes(job_id) or b"", int(row["expected_min_pages"] or 0))
                result["note"] = info
                if not ok_pdf:
                    result["actual"] = "succeeded nhưng PDF không hợp lệ"
    except Exception as exc:
        result["actual"] = "error"
        result["note"] = f"{type(exc).__name__}: {exc}"
    result["seconds"] = round(time.perf_counter() - t0, 2)
    result["passed"] = result["actual"] == row["expected_status"]
    return result


def main() -> None:
    rows = list(csv.DictReader((DATA / "expected.csv").open(encoding="utf-8")))
    print(f"API: {API_URL} | {len(rows)} file | bắt đầu {datetime.now().isoformat(timespec='seconds')}\n")
    results = []
    for row in rows:
        res = run_one(row)
        results.append(res)
        print(f"{'ĐẠT ' if res['passed'] else 'LỖI '} {res['filename']:<34} mong đợi {res['expected']:<10} thực tế {res['actual']}")

    out = ROOT / "tests" / "integration_results.csv"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    passed = sum(r["passed"] for r in results)
    print(f"\n| Tổng | Đạt | Không đạt |\n|---|---|---|\n| {len(results)} | {passed} | {len(results) - passed} |")
    print(f"Chi tiết: {out}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
