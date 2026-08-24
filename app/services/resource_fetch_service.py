"""资材获取：根目录 CRUD、路径困禁、目录浏览与文件夹打包。"""
from __future__ import annotations

import os
import re
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.api.deps import CurrentUser
from app.core.database import execute, execute_many, fetch_all, fetch_one
from app.core.paths import get_storage_dir
from app.services.resource_fetch_schema import (
    CREATE_RESOURCE_FETCH_DOWNLOAD_LOGS_SQL,
    CREATE_RESOURCE_FETCH_ROOT_ROLES_SQL,
    CREATE_RESOURCE_FETCH_ROOTS_SQL,
)

ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz"}
SKIP_DIR_NAMES = frozenset({"$recycle.bin", "system volume information"})
MAX_ZIP_BYTES = 2 * 1024 * 1024 * 1024
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_TABLES_READY = False


class ZipTooLargeError(Exception):
    pass


def ensure_tables() -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    execute(CREATE_RESOURCE_FETCH_ROOTS_SQL)
    execute(CREATE_RESOURCE_FETCH_ROOT_ROLES_SQL)
    execute(CREATE_RESOURCE_FETCH_DOWNLOAD_LOGS_SQL)
    _TABLES_READY = True


def _dt_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).strip()
    return text or None


def _looks_like_unc(text: str) -> bool:
    return text.startswith("\\\\") or text.startswith("//")


def normalize_root_path(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="请填写绝对路径")
    path = Path(text).expanduser()
    if not path.is_absolute() and not _looks_like_unc(text):
        raise HTTPException(status_code=400, detail="请填写绝对路径（如 C:\\share 或 \\\\nas\\share）")
    try:
        if path.exists():
            return str(path.resolve())
    except OSError:
        pass
    return str(path)


def parse_rel(rel: str | None) -> str:
    text = str(rel or "").strip().replace("\\", "/")
    if not text or text == ".":
        return ""
    if text.startswith("/") or text.startswith("//") or _DRIVE_RE.match(text):
        raise HTTPException(status_code=400, detail="非法路径")
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == ".." or ":" in part:
            raise HTTPException(status_code=400, detail="非法路径")
        parts.append(part)
    return "/".join(parts)


def _is_under(target: Path, root: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        if os.name == "nt":
            t = os.path.normcase(str(target))
            r = os.path.normcase(str(root)).rstrip("\\/")
            return t == r or t.startswith(r + "\\")
        return False


def resolve_under_root(root: Path, rel: str) -> Path:
    try:
        root_resolved = root.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"目录不可访问：{exc}") from exc
    safe_rel = parse_rel(rel)
    target = root_resolved
    if safe_rel:
        for part in safe_rel.split("/"):
            target = target / part
    try:
        resolved = target.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"路径不可访问：{exc}") from exc
    if not _is_under(resolved, root_resolved):
        raise HTTPException(status_code=400, detail="非法文件路径")
    return resolved


def _skip_name(name: str) -> bool:
    if not name or name.startswith("."):
        return True
    return name.lower() in SKIP_DIR_NAMES


def _kind_of(path: Path) -> str:
    if path.is_dir():
        return "dir"
    if path.suffix.lower() in ARCHIVE_EXTS:
        return "archive"
    return "file"


def _item_rel(parent_rel: str, name: str) -> str:
    return f"{parent_rel}/{name}" if parent_rel else name


def public_root_dict(row: dict) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": str(row.get("name") or ""),
        "remark": str(row.get("remark") or ""),
    }


def _normalize_role_ids(raw: list[int] | None, *, required: bool) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for item in raw or []:
        try:
            role_id = int(item)
        except (TypeError, ValueError):
            continue
        if role_id <= 0 or role_id in seen:
            continue
        seen.add(role_id)
        ids.append(role_id)
    if required and not ids:
        raise HTTPException(status_code=400, detail="请至少选择一个可访问角色")
    if not ids:
        return []
    placeholders = ", ".join(["%s"] * len(ids))
    rows = fetch_all(f"SELECT id FROM roles WHERE id IN ({placeholders})", tuple(ids))
    found = {int(r["id"]) for r in rows}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(status_code=400, detail="所选角色不存在，请刷新后重试")
    return ids


def _replace_root_roles(root_id: int, role_ids: list[int]) -> None:
    execute("DELETE FROM resource_fetch_root_roles WHERE root_id = %s", (root_id,))
    if role_ids:
        execute_many(
            "INSERT INTO resource_fetch_root_roles (root_id, role_id) VALUES (%s, %s)",
            [(root_id, rid) for rid in role_ids],
        )


def _roles_map_for_roots(root_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {int(i): [] for i in root_ids}
    if not root_ids:
        return result
    placeholders = ", ".join(["%s"] * len(root_ids))
    rows = fetch_all(
        f"""
        SELECT rr.root_id, ro.id, ro.code, ro.name_zh
        FROM resource_fetch_root_roles rr
        INNER JOIN roles ro ON ro.id = rr.role_id
        WHERE rr.root_id IN ({placeholders})
        ORDER BY ro.id ASC
        """,
        tuple(int(i) for i in root_ids),
    )
    for row in rows:
        result.setdefault(int(row["root_id"]), []).append(
            {
                "id": int(row["id"]),
                "code": str(row.get("code") or ""),
                "name_zh": str(row.get("name_zh") or ""),
            }
        )
    return result


def admin_root_dict(row: dict, roles: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    role_items = roles if roles is not None else _roles_map_for_roots([int(row["id"])]).get(int(row["id"]), [])
    return {
        "id": int(row["id"]),
        "name": str(row.get("name") or ""),
        "absolute_path": str(row.get("absolute_path") or ""),
        "enabled": bool(int(row.get("enabled") or 0)),
        "sort_order": int(row.get("sort_order") or 0),
        "remark": str(row.get("remark") or ""),
        "role_ids": [int(r["id"]) for r in role_items],
        "roles": role_items,
        "created_by": str(row.get("created_by") or ""),
        "updated_by": str(row.get("updated_by") or ""),
        "created_at": _dt_str(row.get("created_at")),
        "updated_at": _dt_str(row.get("updated_at")),
        "path_exists": _path_exists(str(row.get("absolute_path") or "")),
    }


def user_can_access_root(root_id: int, user: CurrentUser, roles_by_root: dict[int, list[dict[str, Any]]] | None = None) -> bool:
    """无角色配置（旧数据）= 所有登录用户可访问；有配置则仅所列角色。"""
    if roles_by_root is None:
        assigned = _roles_map_for_roots([root_id]).get(int(root_id), [])
    else:
        assigned = roles_by_root.get(int(root_id), [])
    if not assigned:
        return True
    user_role_id = int(user.role_id or 0)
    return any(int(r["id"]) == user_role_id for r in assigned)


def assert_user_can_access_root(root_id: int, user: CurrentUser) -> None:
    if not user_can_access_root(root_id, user):
        raise HTTPException(status_code=403, detail="无权查看或下载该目录")


def _path_exists(raw: str) -> bool:
    if not raw:
        return False
    try:
        return Path(raw).expanduser().exists()
    except OSError:
        return False


def list_enabled_roots(user: CurrentUser) -> list[dict[str, Any]]:
    ensure_tables()
    rows = fetch_all(
        """
        SELECT id, name, remark
        FROM resource_fetch_roots
        WHERE enabled = 1
        ORDER BY sort_order ASC, id ASC
        """
    )
    if not rows:
        return []
    roles_by_root = _roles_map_for_roots([int(r["id"]) for r in rows])
    return [public_root_dict(r) for r in rows if user_can_access_root(int(r["id"]), user, roles_by_root)]


def list_accessible_root_ids(user: CurrentUser) -> list[int]:
    """含已停用目录：只要角色仍有权限，即可看该目录的下载记录。"""
    ensure_tables()
    rows = fetch_all("SELECT id FROM resource_fetch_roots")
    if not rows:
        return []
    roles_by_root = _roles_map_for_roots([int(r["id"]) for r in rows])
    return [int(r["id"]) for r in rows if user_can_access_root(int(r["id"]), user, roles_by_root)]


def record_download(
    *,
    root_id: int,
    root_name: str,
    rel_path: str,
    file_name: str,
    item_kind: str,
    user: CurrentUser,
) -> None:
    ensure_tables()
    execute(
        """
        INSERT INTO resource_fetch_download_logs
            (root_id, root_name, rel_path, file_name, item_kind,
             downloaded_by, downloaded_by_user_id, downloaded_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            int(root_id),
            (root_name or "")[:128],
            (rel_path or "")[:1024],
            (file_name or "")[:255],
            (item_kind or "file")[:16],
            (user.name or user.username or "")[:64],
            user.id,
            datetime.now(),
        ),
    )


def list_download_logs(
    user: CurrentUser,
    *,
    root_id: int | None = None,
    keyword: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    ensure_tables()
    allowed = list_accessible_root_ids(user)
    if not allowed:
        return {"items": [], "total": 0}
    if root_id is not None:
        if int(root_id) not in allowed:
            raise HTTPException(status_code=403, detail="无权查看该目录的下载记录")
        allowed = [int(root_id)]
    placeholders = ", ".join(["%s"] * len(allowed))
    where = f"root_id IN ({placeholders})"
    params: list[Any] = [int(i) for i in allowed]
    text = (keyword or "").strip()
    if text:
        like = f"%{text}%"
        where += " AND (root_name LIKE %s OR file_name LIKE %s OR rel_path LIKE %s OR downloaded_by LIKE %s)"
        params.extend([like, like, like, like])
    total_row = fetch_one(
        f"SELECT COUNT(*) AS cnt FROM resource_fetch_download_logs WHERE {where}",
        tuple(params),
    )
    total = int((total_row or {}).get("cnt") or 0)
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    rows = fetch_all(
        f"""
        SELECT id, root_id, root_name, rel_path, file_name, item_kind,
               downloaded_by, downloaded_by_user_id, downloaded_at
        FROM resource_fetch_download_logs
        WHERE {where}
        ORDER BY downloaded_at DESC, id DESC
        LIMIT %s OFFSET %s
        """,
        (*params, limit, offset),
    )
    items = [
        {
            "id": int(r["id"]),
            "root_id": int(r["root_id"]),
            "root_name": str(r.get("root_name") or ""),
            "rel_path": str(r.get("rel_path") or ""),
            "file_name": str(r.get("file_name") or ""),
            "item_kind": str(r.get("item_kind") or "file"),
            "downloaded_by": str(r.get("downloaded_by") or ""),
            "downloaded_by_user_id": int(r["downloaded_by_user_id"]) if r.get("downloaded_by_user_id") else None,
            "downloaded_at": _dt_str(r.get("downloaded_at")),
        }
        for r in rows
    ]
    return {"items": items, "total": total}


def list_admin_roots() -> list[dict[str, Any]]:
    ensure_tables()
    rows = fetch_all(
        """
        SELECT *
        FROM resource_fetch_roots
        ORDER BY sort_order ASC, id ASC
        """
    )
    roles_by_root = _roles_map_for_roots([int(r["id"]) for r in rows]) if rows else {}
    return [admin_root_dict(r, roles_by_root.get(int(r["id"]), [])) for r in rows]


def get_root_or_404(root_id: int, *, enabled_only: bool) -> dict:
    ensure_tables()
    row = fetch_one("SELECT * FROM resource_fetch_roots WHERE id = %s", (root_id,))
    if not row:
        raise HTTPException(status_code=404, detail="根目录不存在")
    if enabled_only and not int(row.get("enabled") or 0):
        raise HTTPException(status_code=404, detail="根目录未启用")
    return row


def _assert_unique_path(absolute_path: str, exclude_id: int | None = None) -> None:
    if os.name == "nt":
        rows = fetch_all("SELECT id, absolute_path FROM resource_fetch_roots")
        needle = os.path.normcase(absolute_path)
        for row in rows:
            if exclude_id is not None and int(row["id"]) == exclude_id:
                continue
            if os.path.normcase(str(row.get("absolute_path") or "")) == needle:
                raise HTTPException(status_code=400, detail="该路径已配置")
        return
    sql = "SELECT id FROM resource_fetch_roots WHERE absolute_path = %s"
    params: tuple[Any, ...] = (absolute_path,)
    if exclude_id is not None:
        sql += " AND id <> %s"
        params = (absolute_path, exclude_id)
    if fetch_one(sql, params):
        raise HTTPException(status_code=400, detail="该路径已配置")


def create_root(
    *,
    name: str,
    absolute_path: str,
    enabled: bool,
    sort_order: int,
    remark: str,
    allowed_role_ids: list[int] | None,
    user: CurrentUser,
) -> dict:
    ensure_tables()
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="请填写目录名称")
    path = normalize_root_path(absolute_path)
    _assert_unique_path(path)
    role_ids = _normalize_role_ids(allowed_role_ids, required=True)
    new_id = execute(
        """
        INSERT INTO resource_fetch_roots
            (name, absolute_path, enabled, sort_order, remark,
             created_by, created_by_user_id, updated_by, updated_by_user_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            name[:128],
            path[:1024],
            1 if enabled else 0,
            int(sort_order),
            (remark or "")[:255],
            user.name,
            user.id,
            user.name,
            user.id,
        ),
    )
    _replace_root_roles(int(new_id), role_ids)
    row = fetch_one("SELECT * FROM resource_fetch_roots WHERE id = %s", (new_id,))
    if not row:
        raise HTTPException(status_code=500, detail="创建失败")
    return admin_root_dict(row)


def update_root(
    root_id: int,
    *,
    name: str | None,
    absolute_path: str | None,
    enabled: bool | None,
    sort_order: int | None,
    remark: str | None,
    allowed_role_ids: list[int] | None,
    user: CurrentUser,
) -> dict:
    ensure_tables()
    row = get_root_or_404(root_id, enabled_only=False)
    new_name = name.strip() if name is not None else str(row.get("name") or "")
    if not new_name:
        raise HTTPException(status_code=400, detail="请填写目录名称")
    new_path = normalize_root_path(absolute_path) if absolute_path is not None else str(row.get("absolute_path") or "")
    _assert_unique_path(new_path, exclude_id=root_id)
    new_enabled = int(row.get("enabled") or 0) if enabled is None else (1 if enabled else 0)
    new_sort = int(row.get("sort_order") or 0) if sort_order is None else int(sort_order)
    new_remark = str(row.get("remark") or "") if remark is None else str(remark)
    execute(
        """
        UPDATE resource_fetch_roots
        SET name=%s, absolute_path=%s, enabled=%s, sort_order=%s, remark=%s,
            updated_by=%s, updated_by_user_id=%s
        WHERE id=%s
        """,
        (
            new_name[:128],
            new_path[:1024],
            new_enabled,
            new_sort,
            new_remark[:255],
            user.name,
            user.id,
            root_id,
        ),
    )
    if allowed_role_ids is not None:
        _replace_root_roles(root_id, _normalize_role_ids(allowed_role_ids, required=True))
    updated = fetch_one("SELECT * FROM resource_fetch_roots WHERE id = %s", (root_id,))
    if not updated:
        raise HTTPException(status_code=404, detail="根目录不存在")
    return admin_root_dict(updated)


def delete_root(root_id: int) -> None:
    ensure_tables()
    get_root_or_404(root_id, enabled_only=False)
    execute("DELETE FROM resource_fetch_root_roles WHERE root_id = %s", (root_id,))
    execute("DELETE FROM resource_fetch_roots WHERE id = %s", (root_id,))


def root_path(row: dict) -> Path:
    raw = str(row.get("absolute_path") or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="根目录路径无效")
    return Path(raw).expanduser()


def browse(root_id: int, rel: str | None, user: CurrentUser) -> dict[str, Any]:
    row = get_root_or_404(root_id, enabled_only=True)
    assert_user_can_access_root(int(row["id"]), user)
    safe_rel = parse_rel(rel)
    root = root_path(row)
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=400, detail="目录不存在或不可访问")
    target = resolve_under_root(root, safe_rel)
    if not target.exists():
        raise HTTPException(status_code=404, detail="目录不存在或不可访问")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="该路径不是文件夹")

    items: list[dict[str, Any]] = []
    try:
        entries = list(os.scandir(target))
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"无法读取目录：{exc}") from exc

    root_resolved = root.resolve()
    for entry in entries:
        if _skip_name(entry.name):
            continue
        try:
            if entry.is_symlink():
                continue
            is_dir = entry.is_dir(follow_symlinks=False)
            is_file = entry.is_file(follow_symlinks=False)
        except OSError:
            continue
        if not is_dir and not is_file:
            continue
        entry_path = Path(entry.path)
        try:
            resolved = entry_path.resolve()
        except OSError:
            continue
        if not _is_under(resolved, root_resolved):
            continue
        try:
            stat = entry.stat(follow_symlinks=False)
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            size = 0 if is_dir else int(stat.st_size)
        except OSError:
            mtime = None
            size = 0
        kind = "dir" if is_dir else _kind_of(entry_path)
        items.append(
            {
                "name": entry.name,
                "kind": kind,
                "size": size,
                "mtime": mtime,
                "rel": _item_rel(safe_rel, entry.name),
            }
        )

    items.sort(key=lambda x: (0 if x["kind"] == "dir" else 1, str(x["name"]).lower()))

    crumbs: list[dict[str, str]] = [{"name": str(row.get("name") or ""), "rel": ""}]
    if safe_rel:
        acc: list[str] = []
        for part in safe_rel.split("/"):
            acc.append(part)
            crumbs.append({"name": part, "rel": "/".join(acc)})

    return {
        "root_id": int(row["id"]),
        "root_name": str(row.get("name") or ""),
        "rel": safe_rel,
        "breadcrumbs": crumbs,
        "items": items,
    }


def _zip_root_name(folder_name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", folder_name).strip() or "folder"
    return name[:120]


def build_dir_zip(src_dir: Path, zip_root_name: str, dest: Path) -> None:
    root_resolved = src_dir.resolve()
    total = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, allowZip64=True, strict_timestamps=False) as zf:
        for dirpath, dirnames, filenames in os.walk(src_dir, followlinks=False):
            dirnames[:] = [d for d in dirnames if not _skip_name(d)]
            current = Path(dirpath)
            try:
                if not _is_under(current.resolve(), root_resolved):
                    dirnames[:] = []
                    continue
            except OSError:
                dirnames[:] = []
                continue
            rel_dir = current.relative_to(src_dir)
            for name in filenames:
                if _skip_name(name):
                    continue
                full = current / name
                try:
                    if full.is_symlink() or not full.is_file():
                        continue
                    resolved = full.resolve()
                except OSError:
                    continue
                if not _is_under(resolved, root_resolved):
                    continue
                try:
                    size = resolved.stat().st_size
                except OSError:
                    continue
                total += size
                if total > MAX_ZIP_BYTES:
                    raise ZipTooLargeError()
                arc = Path(zip_root_name) / rel_dir / name
                zf.write(resolved, arcname=str(arc).replace("\\", "/"))
        if dest.stat().st_size > MAX_ZIP_BYTES:
            raise ZipTooLargeError()


def prepare_download(root_id: int, rel: str | None, user: CurrentUser) -> tuple[Path, str, str, bool]:
    """返回 (path, download_name, media_type, is_temp_zip)。"""
    row = get_root_or_404(root_id, enabled_only=True)
    assert_user_can_access_root(int(row["id"]), user)
    safe_rel = parse_rel(rel)
    root = root_path(row)
    if not root.exists():
        raise HTTPException(status_code=400, detail="目录不存在或不可访问")
    target = resolve_under_root(root, safe_rel)
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件或目录不存在")
    if target.is_symlink():
        raise HTTPException(status_code=400, detail="不支持下载该路径")

    if target.is_file():
        kind = "archive" if _kind_of(target) == "archive" else "file"
        record_download(
            root_id=int(row["id"]),
            root_name=str(row.get("name") or ""),
            rel_path=safe_rel,
            file_name=target.name,
            item_kind=kind,
            user=user,
        )
        return target, target.name, "application/octet-stream", False

    if not target.is_dir():
        raise HTTPException(status_code=400, detail="不支持下载该路径")

    folder_name = target.name if safe_rel else str(row.get("name") or target.name)
    download_name = f"{_zip_root_name(folder_name)}.zip"
    tmp_dir = get_storage_dir() / "resource_fetch_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="rf_", dir=str(tmp_dir))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        build_dir_zip(target, _zip_root_name(folder_name), tmp_path)
    except ZipTooLargeError:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="文件夹过大（超过 2GB），请进入子目录后分批下载")
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    record_download(
        root_id=int(row["id"]),
        root_name=str(row.get("name") or ""),
        rel_path=safe_rel,
        file_name=download_name,
        item_kind="dir",
        user=user,
    )
    return tmp_path, download_name, "application/zip", True
