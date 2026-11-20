import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.errors import error_body, install_handlers, request_id_var
from app.logging_setup import setup_logging
from app.routers import auth, health, internal, jobs, operator
from app.storage import get_storage

setup_logging()
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    for i in range(10):  # đợi object storage sẵn sàng
        try:
            await asyncio.to_thread(get_storage().ensure_buckets)
            break
        except Exception as e:
            log.warning("storage not ready (%s), retry %d/10", type(e).__name__, i + 1)
            await asyncio.sleep(2)
    yield


app = FastAPI(title="Doc2PDF API", version="1.0.0", lifespan=lifespan)
install_handlers(app)
@app.middleware("http")
async def request_context(request, call_next):
    rid = (request.headers.get("x-request-id") or uuid.uuid4().hex)[:64]
    token = request_id_var.set(rid)
    start = time.perf_counter()
    try:
        # Chỉ chặn sớm body cực lớn (> 5 lần giới hạn). File vượt 10 MB thông thường vẫn được đọc hết để
        # trả JSON 413 rõ ràng cho frontend (chặn sớm hơn sẽ làm trình duyệt chỉ thấy lỗi mạng).
        limit = get_settings().upload_limit_bytes * 5
        length = request.headers.get("content-length", "")
        if request.method == "POST" and request.url.path == "/jobs" and length.isdigit() \
                and int(length) > limit:
            # Từ chối trước khi framework đọc cả body vào bộ nhớ/đĩa
            response = JSONResponse(error_body(
                "FILE_TOO_LARGE", f"File vượt quá {get_settings().max_upload_mb} MB"), status_code=413)
        else:
            response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    if request.url.path not in ("/health", "/ready"):
        # Chỉ log path (không log query/header) để không lộ token hay URL đã ký
        logging.getLogger("app.access").info(
            "%s %s -> %d (%.0f ms) req=%s", request.method, request.url.path,
            response.status_code, (time.perf_counter() - start) * 1000, rid)
    return response


# CORS phải là lớp ngoài cùng (thêm sau cùng) để phản hồi lỗi sớm vẫn có header CORS
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origin_list,
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Request-ID", "Location"])

for r in (health.router, auth.router, jobs.router, operator.router):
    app.include_router(r)
# Worker gọi /api/internal/jobs/...; /internal/jobs/... là bí danh không hiện trong tài liệu OpenAPI
app.include_router(internal.router, prefix="/api/internal")
app.include_router(internal.router, prefix="/internal", include_in_schema=False)
