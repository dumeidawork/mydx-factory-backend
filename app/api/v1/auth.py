"""认证相关 API：登录、当前用户、用户管理（管理员开户/维护）。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.core.database import execute, fetch_all, fetch_one
from app.core.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["认证"])

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,64}$")
_MIN_PASSWORD_LEN = 6
_ADMIN_ROLES = frozenset({"super_admin", "general_manager"})


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


class CreateUserRequest(BaseModel):
    username: str
    password: str
    name: str
    role_id: int = 4
    department: str | None = None
    language_preference: str = "zh-CN"


class UpdateUserRequest(BaseModel):
    name: str | None = None
    role_id: int | None = None
    department: str | None = None
    language_preference: str | None = None
    password: str | None = None


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=1)


def _user_public_dict(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "name": row.get("name") or "",
        "username": row.get("username") or "",
        "role_id": int(row["role_id"]),
        "role_code": row.get("role_code") or "",
        "role_name_zh": row.get("role_name_zh") or "",
        "department": row.get("department"),
        "language_preference": row.get("language_preference") or "zh-CN",
        "created_at": str(row["created_at"]) if row.get("created_at") is not None else None,
    }


def _require_admin(user: CurrentUser) -> None:
    if user.role_code not in _ADMIN_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可执行此操作")


def _fetch_user_row(user_id: int) -> dict | None:
    return fetch_one(
        """
        SELECT u.id, u.name, u.username, u.role_id, u.department, u.language_preference, u.created_at,
               r.code AS role_code, r.name_zh AS role_name_zh
        FROM users u
        LEFT JOIN roles r ON r.id = u.role_id
        WHERE u.id = %s
        """,
        (user_id,),
    )


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest):
    username = body.username.strip()
    row = fetch_one(
        """
        SELECT u.id, u.name, u.username, u.password_hash, u.role_id, u.department, u.language_preference, u.created_at,
               r.code AS role_code, r.name_zh AS role_name_zh
        FROM users u
        LEFT JOIN roles r ON r.id = u.role_id
        WHERE u.username = %s
        """,
        (username,),
    )
    if not row or not verify_password(body.password, row.get("password_hash")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    token = create_access_token(row["id"], {"username": row["username"], "role_id": row["role_id"]})
    return LoginResponse(access_token=token, user=_user_public_dict(row))


@router.get("/me")
def get_current_user_info(user: Annotated[CurrentUser, Depends(get_current_user)]):
    return {
        "id": user.id,
        "name": user.name,
        "username": user.username,
        "role_id": user.role_id,
        "role_code": user.role_code,
        "role_name_zh": user.role_name_zh,
        "department": user.department,
        "language_preference": user.language_preference,
    }


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    """任意已登录用户可自助修改自己的密码（需校验旧密码）。"""
    if len(body.new_password) < _MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"新密码至少 {_MIN_PASSWORD_LEN} 位")
    if body.old_password == body.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与旧密码相同")
    row = fetch_one("SELECT id, password_hash FROM users WHERE id = %s", (user.id,))
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not verify_password(body.old_password, row.get("password_hash")):
        raise HTTPException(status_code=400, detail="旧密码不正确")
    execute(
        "UPDATE users SET password_hash=%s WHERE id=%s",
        (hash_password(body.new_password), user.id),
    )
    return {"ok": True, "message": "密码已修改，请使用新密码登录"}


@router.get("/roles")
def list_roles(user: Annotated[CurrentUser, Depends(get_current_user)]):
    _require_admin(user)
    rows = fetch_all(
        """
        SELECT id, code, name_zh, name_en, name_ja
        FROM roles
        ORDER BY id
        """
    )
    return {
        "items": [
            {
                "id": int(r["id"]),
                "code": r.get("code") or "",
                "name_zh": r.get("name_zh") or "",
                "name_en": r.get("name_en") or "",
                "name_ja": r.get("name_ja") or "",
            }
            for r in rows
        ]
    }


@router.get("/users")
def list_users(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    q: str = Query("", max_length=64),
    role_id: int | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    _require_admin(user)
    clauses = ["u.username IS NOT NULL"]
    params: list = []
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        clauses.append("(u.name LIKE %s OR u.username LIKE %s OR IFNULL(u.department,'') LIKE %s)")
        params.extend([like, like, like])
    if role_id is not None:
        clauses.append("u.role_id = %s")
        params.append(role_id)
    where = " AND ".join(clauses)
    total_row = fetch_one(
        f"SELECT COUNT(*) AS cnt FROM users u WHERE {where}",
        tuple(params),
    )
    rows = fetch_all(
        f"""
        SELECT u.id, u.name, u.username, u.role_id, u.department, u.language_preference, u.created_at,
               r.code AS role_code, r.name_zh AS role_name_zh
        FROM users u
        LEFT JOIN roles r ON r.id = u.role_id
        WHERE {where}
        ORDER BY u.id
        LIMIT %s OFFSET %s
        """,
        tuple(params + [limit, offset]),
    )
    return {
        "total": int((total_row or {}).get("cnt") or 0),
        "items": [_user_public_dict(r) for r in rows],
    }


@router.get("/users/suggest")
def suggest_users(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    q: str = Query("", max_length=64),
    limit: int = Query(20, ge=1, le=50),
):
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        rows = fetch_all(
            """
            SELECT u.id, u.name, u.username, u.role_id, u.department, u.language_preference, u.created_at,
                   r.code AS role_code, r.name_zh AS role_name_zh
            FROM users u
            LEFT JOIN roles r ON r.id = u.role_id
            WHERE u.username IS NOT NULL
              AND (u.name LIKE %s OR u.username LIKE %s)
            ORDER BY u.name
            LIMIT %s
            """,
            (like, like, limit),
        )
    else:
        rows = fetch_all(
            """
            SELECT u.id, u.name, u.username, u.role_id, u.department, u.language_preference, u.created_at,
                   r.code AS role_code, r.name_zh AS role_name_zh
            FROM users u
            LEFT JOIN roles r ON r.id = u.role_id
            WHERE u.username IS NOT NULL
            ORDER BY u.name
            LIMIT %s
            """,
            (limit,),
        )
    return {"items": [_user_public_dict(r) for r in rows]}


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    body: CreateUserRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    _require_admin(user)
    username = body.username.strip()
    name = body.name.strip()
    if not _USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail="用户名须为 3–64 位字母、数字或下划线")
    if len(body.password) < _MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"密码至少 {_MIN_PASSWORD_LEN} 位")
    if not name:
        raise HTTPException(status_code=400, detail="显示名不能为空")
    role = fetch_one("SELECT id FROM roles WHERE id = %s", (body.role_id,))
    if not role:
        raise HTTPException(status_code=400, detail="角色不存在")
    exists = fetch_one("SELECT id FROM users WHERE username = %s", (username,))
    if exists:
        raise HTTPException(status_code=400, detail="用户名已存在")
    new_id = execute(
        """
        INSERT INTO users (name, role_id, department, language_preference, username, password_hash)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            name,
            body.role_id,
            (body.department or "").strip() or None,
            body.language_preference or "zh-CN",
            username,
            hash_password(body.password),
        ),
    )
    row = _fetch_user_row(new_id)
    return _user_public_dict(row or {"id": new_id, "name": name, "username": username, "role_id": body.role_id})


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    body: UpdateUserRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    _require_admin(user)
    existing = _fetch_user_row(user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="用户不存在")

    name = existing["name"]
    role_id = int(existing["role_id"])
    department = existing.get("department")
    language_preference = existing.get("language_preference") or "zh-CN"
    password_hash = None

    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="显示名不能为空")
    if body.role_id is not None:
        role = fetch_one("SELECT id FROM roles WHERE id = %s", (body.role_id,))
        if not role:
            raise HTTPException(status_code=400, detail="角色不存在")
        role_id = body.role_id
    if body.department is not None:
        department = body.department.strip() or None
    if body.language_preference is not None:
        language_preference = body.language_preference or "zh-CN"
    if body.password is not None and body.password.strip():
        if len(body.password) < _MIN_PASSWORD_LEN:
            raise HTTPException(status_code=400, detail=f"密码至少 {_MIN_PASSWORD_LEN} 位")
        password_hash = hash_password(body.password)

    if password_hash is not None:
        execute(
            """
            UPDATE users
            SET name=%s, role_id=%s, department=%s, language_preference=%s, password_hash=%s
            WHERE id=%s
            """,
            (name, role_id, department, language_preference, password_hash, user_id),
        )
    else:
        execute(
            """
            UPDATE users
            SET name=%s, role_id=%s, department=%s, language_preference=%s
            WHERE id=%s
            """,
            (name, role_id, department, language_preference, user_id),
        )

    row = _fetch_user_row(user_id)
    return _user_public_dict(row or existing)
