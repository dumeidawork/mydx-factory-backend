"""法国许可证模块 API：预览与生成。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.api.v1.contract_archives import _absolute_from_relative, _assert_archive_visible, _get_archive_or_404
from app.core.file_log import append_log_line
from app.core.paths import get_storage_dir
from app.services.license_service import (
    build_license_zip,
    is_france_order_no,
    normalize_order_no,
    parse_batch_date,
    preview_payload,
)

LOG_DIR = get_storage_dir() / "logs"

router = APIRouter(prefix="/license", tags=["许可证"])


class GenerateLicenseRequest(BaseModel):
    order_no: str = Field(..., min_length=1)
    inspector_name: str = ""
    pad_item_no: bool = False
    batch_date: str | None = None
    archive_ids: list[int] = Field(default_factory=list)


def _require_france_order(order_no: str) -> str:
    digits = normalize_order_no(order_no)
    if not digits:
        raise HTTPException(status_code=400, detail="请输入订单号")
    if not is_france_order_no(digits):
        raise HTTPException(status_code=400, detail="许可证仅支持法国订单（订单号以 2 开头）")
    return digits


def _collect_contract_files(archive_ids: list[int], user: CurrentUser) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    seen: set[int] = set()
    for raw_id in archive_ids:
        archive_id = int(raw_id)
        if archive_id <= 0 or archive_id in seen:
            continue
        seen.add(archive_id)
        row = _get_archive_or_404(archive_id)
        _assert_archive_visible(row, user)
        path = _absolute_from_relative(str(row["storage_relative_path"]))
        files.append((str(row.get("file_name") or path.name), path))
    return files


def _file_download_response(path: Path, download_name: str, media_type: str) -> FileResponse:
    def _unlink() -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    return FileResponse(
        str(path),
        media_type=media_type,
        filename=download_name,
        background=BackgroundTask(_unlink),
    )


@router.get("/preview")
def preview_license(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    order_no: str = Query(..., min_length=1),
):
    del user
    digits = _require_france_order(order_no)
    data = preview_payload(digits)
    if not data["has_source"]:
        raise HTTPException(status_code=404, detail="订单号未出现在法国客户箱单或订单明细中")
    return data


@router.post("/generate")
def generate_license(
    body: GenerateLicenseRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    digits = _require_france_order(body.order_no)
    try:
        batch_date = parse_batch_date(body.batch_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    inspector_name = str(body.inspector_name or "").strip()
    contract_files = _collect_contract_files(body.archive_ids, user)
    try:
        zip_path, file_names = build_license_zip(
            order_no=digits,
            pad_item_no=bool(body.pad_item_no),
            inspector_name=inspector_name,
            batch_date_value=batch_date,
            contract_files=contract_files,
            user_id=int(user.id),
            user_name=str(user.name or ""),
        )
    except FileNotFoundError as exc:
        append_log_line(
            LOG_DIR,
            f"license_{datetime.now().strftime('%Y-%m-%d')}.log",
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} generate order_no={digits} {exc}",
        )
        if "许可证模板" in str(exc):
            detail = "许可证模板缺失，请联系管理员"
        elif "合同归档" in str(exc):
            detail = "合同归档文件不存在或已被移动"
        else:
            detail = "生成失败，请联系管理员"
        raise HTTPException(status_code=500, detail=detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    del file_names
    return _file_download_response(
        zip_path,
        f"{digits}许可证.zip",
        "application/zip",
    )
