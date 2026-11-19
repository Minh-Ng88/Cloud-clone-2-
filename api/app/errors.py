import contextvars
import logging

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
log = logging.getLogger("app.errors")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None,
                 headers: dict | None = None, detail: str | None = None):
        self.status, self.code, self.message = status, code, message
        self.details, self.headers, self.detail = details, headers, detail


def error_body(code: str, message: str, details=None, detail: str | None = None) -> dict:
    err = {"code": code, "message": message, "request_id": request_id_var.get()}
    if details:
        err["details"] = details
    body = {"error": err}
    if detail:  # chuỗi tiếng Anh ngắn ở cấp cao nhất, cho client đọc `detail` (worker /api/internal)
        body["detail"] = detail
    return body


_HTTP_CODES = {400: "BAD_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND",
               405: "METHOD_NOT_ALLOWED", 413: "FILE_TOO_LARGE"}


def install_handlers(app) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(error_body(exc.code, exc.message, exc.details, exc.detail), status_code=exc.status,
                            headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = {"errors": jsonable_encoder(exc.errors(), custom_encoder={Exception: str})}
        return JSONResponse(error_body("VALIDATION_ERROR", "Dữ liệu gửi lên không hợp lệ", details),
                            status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(error_body(code, str(exc.detail)), status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse(error_body("INTERNAL_ERROR", "Lỗi hệ thống"), status_code=500)
