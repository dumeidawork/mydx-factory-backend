"""API 依赖：语言头、当前登录用户等。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from typing_extensions import Annotated

from app.core.database import fetch_one
from app.core.i18n import DEFAULT_LANG, SUPPORTED_LANGS
from app.core.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def get_accept_language(accept_language: str = Header(default="zh-CN")) -> str:
    """从请求头解析客户端语言偏好"""
    if accept_language in SUPPORTED_LANGS:
        return accept_language
    for lang in SUPPORTED_LANGS:
        if lang.startswith(accept_language.split("-")[0]):
            return lang
    return DEFAULT_LANG


@dataclass
class CurrentUser:
    id: int
    name: str
    username: str
    role_id: int
    role_code: str
    role_name_zh: str
    department: str | None = None
    language_preference: str = "zh-CN"


def _load_user_by_id(user_id: int) -> CurrentUser | None:
    row = fetch_one(
        """
        SELECT u.id, u.name, u.username, u.role_id, u.department, u.language_preference,
               r.code AS role_code, r.name_zh AS role_name_zh
        FROM users u
        LEFT JOIN roles r ON r.id = u.role_id
        WHERE u.id = %s
        """,
        (user_id,),
    )
    if not row or not row.get("username"):
        return None
    return CurrentUser(
        id=int(row["id"]),
        name=str(row["name"] or ""),
        username=str(row["username"]),
        role_id=int(row["role_id"]),
        role_code=str(row.get("role_code") or ""),
        role_name_zh=str(row.get("role_name_zh") or ""),
        department=row.get("department"),
        language_preference=str(row.get("language_preference") or "zh-CN"),
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录或令牌缺失")
    payload = decode_access_token(credentials.credentials)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效或已过期")
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效")
    user = _load_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user
