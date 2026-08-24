"""资材获取 API：管理员配置根目录，登录用户浏览并下载。"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.services import resource_fetch_service as svc

router = APIRouter(prefix="/resource-fetch", tags=["资材获取"])
UserDep = Annotated[CurrentUser, Depends(get_current_user)]

_ADMIN_ROLES = frozenset({"super_admin", "general_manager"})


class RootCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    absolute_path: str = Field(..., min_length=1, max_length=1024)
    enabled: bool = True
    sort_order: int = 0
    remark: str = ""
    allowed_role_ids: list[int] = Field(default_factory=list)


class RootUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    absolute_path: str | None = Field(default=None, max_length=1024)
    enabled: bool | None = None
    sort_order: int | None = None
    remark: str | None = Field(default=None, max_length=255)
    allowed_role_ids: list[int] | None = None


def _require_admin(user: CurrentUser) -> None:
    if user.role_code not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")


def _safe_download_name(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch not in '\r\n"').strip() or "download"
    return cleaned[:180]


def _file_download_response(
    path: Path,
    download_name: str,
    media_type: str,
    *,
    is_temp: bool,
) -> FileResponse:
    def _unlink() -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    download_name = _safe_download_name(download_name)
    ascii_name = download_name.encode("ascii", "ignore").decode("ascii") or "download"
    headers = {
        "Content-Disposition": (
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(download_name)}"
        )
    }
    return FileResponse(
        str(path),
        media_type=media_type,
        headers=headers,
        background=BackgroundTask(_unlink) if is_temp else None,
    )


@router.get("/roots")
def list_roots(user: UserDep):
    return {"items": svc.list_enabled_roots(user)}


@router.get("/admin/roots")
def admin_list_roots(user: UserDep):
    _require_admin(user)
    return {"items": svc.list_admin_roots()}


@router.post("/admin/roots")
def admin_create_root(body: RootCreateRequest, user: UserDep):
    _require_admin(user)
    item = svc.create_root(
        name=body.name,
        absolute_path=body.absolute_path,
        enabled=body.enabled,
        sort_order=body.sort_order,
        remark=body.remark or "",
        allowed_role_ids=body.allowed_role_ids,
        user=user,
    )
    return {"item": item}


@router.put("/admin/roots/{root_id}")
def admin_update_root(root_id: int, body: RootUpdateRequest, user: UserDep):
    _require_admin(user)
    item = svc.update_root(
        root_id,
        name=body.name,
        absolute_path=body.absolute_path,
        enabled=body.enabled,
        sort_order=body.sort_order,
        remark=body.remark,
        allowed_role_ids=body.allowed_role_ids,
        user=user,
    )
    return {"item": item}


@router.delete("/admin/roots/{root_id}")
def admin_delete_root(root_id: int, user: UserDep):
    _require_admin(user)
    svc.delete_root(root_id)
    return {"ok": True}


@router.get("/browse")
def browse(
    user: UserDep,
    root_id: int = Query(..., ge=1),
    rel: str = Query(default=""),
):
    return svc.browse(root_id, rel, user)


@router.get("/download")
def download(
    user: UserDep,
    root_id: int = Query(..., ge=1),
    rel: str = Query(default=""),
):
    path, download_name, media_type, is_temp = svc.prepare_download(root_id, rel, user)
    return _file_download_response(path, download_name, media_type, is_temp=is_temp)


@router.get("/download-logs")
def download_logs(
    user: UserDep,
    root_id: int | None = Query(default=None, ge=1),
    q: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return svc.list_download_logs(user, root_id=root_id, keyword=q, limit=limit, offset=offset)
