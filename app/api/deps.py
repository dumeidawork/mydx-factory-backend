"""API 依赖：语言头、当前登录用户等。"""
from __future__ import annotations

import time
from dataclasses import dataclass

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


LAYOUT_MODES = frozenset({"horizontal", "vertical", "classic"})
DEFAULT_LAYOUT_MODE = "horizontal"
COLOR_THEMES = frozenset(
    {
        "classic-blue",
        "morandi-fog",
        "morandi-rose",
        "morandi-sage",
        "macaron-mint",
        "macaron-peach",
        "macaron-lilac",
        "guohua-landscape",
        "guohua-cinnabar",
        "guohua-ink",
        "oil-autumn",
        "oil-venetian",
        "oil-umber",
    }
)
DEFAULT_COLOR_THEME = "classic-blue"
_USER_CACHE_TTL_SEC = 30.0
_USER_CACHE_MAX = 256
_user_cache: dict[int, tuple[float, "CurrentUser"]] = {}


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
    layout_mode: str = DEFAULT_LAYOUT_MODE
    color_theme: str = DEFAULT_COLOR_THEME


def normalize_layout_mode(value: object) -> str:
    mode = str(value or DEFAULT_LAYOUT_MODE).strip()
    return mode if mode in LAYOUT_MODES else DEFAULT_LAYOUT_MODE


def normalize_color_theme(value: object) -> str:
    theme = str(value or DEFAULT_COLOR_THEME).strip()
    return theme if theme in COLOR_THEMES else DEFAULT_COLOR_THEME


def _load_user_by_id(user_id: int) -> CurrentUser | None:
    now = time.monotonic()
    cached = _user_cache.get(user_id)
    if cached and now - cached[0] < _USER_CACHE_TTL_SEC:
        return cached[1]
    row = fetch_one(
        """
        SELECT u.id, u.name, u.username, u.role_id, u.department, u.language_preference, u.layout_mode, u.color_theme,
               r.code AS role_code, r.name_zh AS role_name_zh
        FROM users u
        LEFT JOIN roles r ON r.id = u.role_id
        WHERE u.id = %s
        """,
        (user_id,),
    )
    if not row or not row.get("username"):
        _user_cache.pop(user_id, None)
        return None
    user = CurrentUser(
        id=int(row["id"]),
        name=str(row["name"] or ""),
        username=str(row["username"]),
        role_id=int(row["role_id"]),
        role_code=str(row.get("role_code") or ""),
        role_name_zh=str(row.get("role_name_zh") or ""),
        department=row.get("department"),
        language_preference=str(row.get("language_preference") or "zh-CN"),
        layout_mode=normalize_layout_mode(row.get("layout_mode")),
        color_theme=normalize_color_theme(row.get("color_theme")),
    )
    if len(_user_cache) >= _USER_CACHE_MAX and user_id not in _user_cache:
        oldest = min(_user_cache, key=lambda k: _user_cache[k][0])
        _user_cache.pop(oldest, None)
    _user_cache[user_id] = (now, user)
    return user


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
