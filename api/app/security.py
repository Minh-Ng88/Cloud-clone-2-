import hmac
from datetime import timedelta

import jwt
from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import get_settings
from app.errors import ApiError
from app.timeutil import now_utc

_bearer = HTTPBearer(auto_error=False)
ALGORITHM = "HS256"


class Principal(BaseModel):
    user_id: str
    role: str  # "user" | "operator"

    @property
    def is_operator(self) -> bool:
        return self.role == "operator"


def authenticate(username: str, password: str) -> Principal | None:
    entry = get_settings().accounts.get(username)
    if entry and hmac.compare_digest(entry[0], password):
        return Principal(user_id=username, role=entry[1])
    return None


def create_token(p: Principal) -> tuple[str, int]:
    s = get_settings()
    exp = now_utc() + timedelta(minutes=s.jwt_ttl_minutes)
    token = jwt.encode({"sub": p.user_id, "role": p.role, "exp": exp}, s.jwt_secret, algorithm=ALGORITHM)
    return token, s.jwt_ttl_minutes * 60


def verify_token(token: str) -> Principal:
    """ĐIỂM THAY THẾ khi lên cloud: đổi thân hàm này sang kiểm tra token OIDC (Cognito / Entra / Identity Platform)."""
    try:
        data = jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])
        return Principal(user_id=data["sub"], role=data["role"])
    except (jwt.PyJWTError, KeyError):
        raise ApiError(401, "UNAUTHENTICATED", "Token không hợp lệ hoặc đã hết hạn",
                       headers={"WWW-Authenticate": "Bearer"})


def current_principal(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
    if creds is None:
        raise ApiError(401, "UNAUTHENTICATED", "Thiếu token đăng nhập",
                       headers={"WWW-Authenticate": "Bearer"})
    return verify_token(creds.credentials)


def require_operator(p: Principal = Depends(current_principal)) -> Principal:
    if not p.is_operator:
        raise ApiError(403, "FORBIDDEN", "Chỉ operator mới được dùng chức năng này")
    return p


def require_service(x_service_key: str | None = Header(default=None)) -> None:
    """Chỉ worker (giữ khoá dịch vụ) được gọi /api/internal/*; người dùng không bao giờ có khoá này."""
    if not x_service_key or not hmac.compare_digest(x_service_key, get_settings().internal_service_key):
        raise ApiError(401, "UNAUTHENTICATED", "Service key không hợp lệ", detail="Invalid service key")
