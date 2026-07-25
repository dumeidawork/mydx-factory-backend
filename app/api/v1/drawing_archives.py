"""图纸建档 / 图纸下载 API。"""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.core.database import execute, fetch_all, fetch_one
from app.core.paths import get_drawing_archives_dir

router = APIRouter(prefix="/drawing-archives", tags=["图纸建档"])

ALLOWED_EXTENSIONS = {".pdf"}
_WIN_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TABLES_READY = False


class BatchDownloadRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)
    order_no: str | None = None


def _ensure_tables() -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    execute(
        """
        CREATE TABLE IF NOT EXISTS drawing_archives (
            id INT PRIMARY KEY AUTO_INCREMENT,
            customer_code VARCHAR(64) NOT NULL,
            drawing_no VARCHAR(255) NOT NULL,
            material VARCHAR(128) NOT NULL,
            spec_model VARCHAR(255) NOT NULL,
            drawing_type VARCHAR(128) NOT NULL,
            drawing_name VARCHAR(512) NOT NULL,
            storage_relative_path VARCHAR(1024) NOT NULL,
            file_size BIGINT NOT NULL DEFAULT 0,
            content_type VARCHAR(128) NULL,
            uploaded_at DATETIME NOT NULL,
            uploaded_by_user_id INT NULL,
            uploaded_by_name VARCHAR(64) NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_drawing_name (drawing_name),
            INDEX idx_drawing_no (drawing_no),
            INDEX idx_drawing_customer (customer_code),
            INDEX idx_drawing_uploaded_at (uploaded_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS drawing_archive_logs (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            archive_id INT NULL,
            action VARCHAR(32) NOT NULL,
            operator_user_id INT NULL,
            operator_name VARCHAR(64) NOT NULL DEFAULT '',
            operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            change_summary TEXT NULL,
            INDEX idx_drawing_log_archive (archive_id),
            INDEX idx_drawing_log_action (action),
            INDEX idx_drawing_log_time (operated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    _TABLES_READY = True


def _safe_segment(name: str, fallback: str = "unnamed") -> str:
    cleaned = _WIN_INVALID.sub("_", (name or "").strip())
    cleaned = cleaned.rstrip(" .")
    return cleaned[:120] or fallback


def _safe_drawing_filename(drawing_name: str) -> str:
    cleaned = _WIN_INVALID.sub("_", (drawing_name or "").strip())
    cleaned = cleaned.rstrip(" .")
    return (cleaned or "drawing")[:200]


def _row_to_dict(row: dict) -> dict:
    def fmt(v: Any) -> Any:
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(v, date):
            return v.strftime("%Y-%m-%d")
        return v

    return {k: fmt(v) for k, v in row.items()}


def _write_log(
    archive_id: int | None,
    action: str,
    operator: CurrentUser,
    change_summary: dict | str | None = None,
) -> None:
    summary = change_summary
    if isinstance(summary, dict):
        summary = json.dumps(summary, ensure_ascii=False, default=str)
    execute(
        """
        INSERT INTO drawing_archive_logs
            (archive_id, action, operator_user_id, operator_name, change_summary)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (archive_id, action, operator.id, operator.name, summary),
    )


def _build_drawing_name(
    customer_code: str,
    drawing_no: str,
    material: str,
    spec_model: str,
    drawing_type: str,
) -> str:
    parts = [customer_code, drawing_no, material, spec_model, drawing_type]
    for part in parts:
        if not part:
            raise HTTPException(status_code=400, detail="客户代码、标准号/图纸号-版本号、材质、规格型号/物料号、类型均为必填")
    return ";".join(parts)


def _require_unique_drawing_name(drawing_name: str) -> None:
    row = fetch_one("SELECT id FROM drawing_archives WHERE drawing_name = %s", (drawing_name,))
    if row:
        raise HTTPException(status_code=400, detail=f"图纸已存在（图纸名：{drawing_name}），不允许覆盖，请删除后重传")


def _absolute_from_relative(rel: str) -> Path:
    root = get_drawing_archives_dir().resolve()
    target = (root / rel).resolve()
    if not str(target).startswith(str(root)):
        raise HTTPException(status_code=400, detail="非法文件路径")
    return target


def _get_archive_or_404(archive_id: int) -> dict:
    row = fetch_one("SELECT * FROM drawing_archives WHERE id = %s", (archive_id,))
    if not row:
        raise HTTPException(status_code=404, detail="图纸归档不存在")
    return row


def _save_pdf(customer_code: str, drawing_name: str, upload: UploadFile, raw: bytes) -> tuple[str, str]:
    original_name = Path(upload.filename or "drawing.pdf").name
    ext = Path(original_name).suffix.lower()
    if ext != ".pdf":
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件（.pdf）")
    if not raw:
        raise HTTPException(status_code=400, detail="文件不能为空")

    folder = _safe_segment(customer_code)
    file_stem = _safe_drawing_filename(drawing_name)
    rel_path = f"{folder}/{file_stem}.pdf"
    abs_path = get_drawing_archives_dir() / folder / f"{file_stem}.pdf"
    if abs_path.exists():
        raise HTTPException(status_code=400, detail=f"磁盘上已存在同名文件：{file_stem}.pdf")
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(raw)
    content_type = upload.content_type or "application/pdf"
    return rel_path.replace("\\", "/"), content_type


@router.post("")
async def upload_drawing(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    customer_code: str = Form(...),
    drawing_no: str = Form(...),
    material: str = Form(...),
    spec_model: str = Form(...),
    drawing_type: str = Form(...),
    file: UploadFile = File(...),
):
    _ensure_tables()
    customer = customer_code.strip()
    d_no = drawing_no.strip()
    mat = material.strip()
    spec = spec_model.strip()
    dtype = drawing_type.strip()
    drawing_name = _build_drawing_name(customer, d_no, mat, spec, dtype)
    _require_unique_drawing_name(drawing_name)

    raw = await file.read()
    rel_path, content_type = _save_pdf(customer, drawing_name, file, raw)
    uploaded_at = datetime.now()

    try:
        new_id = execute(
            """
            INSERT INTO drawing_archives (
                customer_code, drawing_no, material, spec_model, drawing_type,
                drawing_name, storage_relative_path, file_size, content_type,
                uploaded_at, uploaded_by_user_id, uploaded_by_name
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                customer,
                d_no,
                mat,
                spec,
                dtype,
                drawing_name,
                rel_path,
                len(raw),
                content_type,
                uploaded_at,
                user.id,
                user.name,
            ),
        )
    except Exception:
        abs_path = _absolute_from_relative(rel_path)
        if abs_path.is_file():
            abs_path.unlink(missing_ok=True)
        raise

    _write_log(
        new_id,
        "upload",
        user,
        {
            "customer_code": customer,
            "drawing_no": d_no,
            "material": mat,
            "spec_model": spec,
            "drawing_type": dtype,
            "drawing_name": drawing_name,
            "path": rel_path,
            "file_size": len(raw),
            "uploaded_at": uploaded_at.strftime("%Y-%m-%d %H:%M:%S"),
            "uploaded_by_name": user.name,
        },
    )
    # 图纸建档后自动清除对应订单行的「待补资料」
    from app.api.v1.workflow import clear_pending_doc_status_for_drawing_no

    cleared = clear_pending_doc_status_for_drawing_no(d_no)
    row = _get_archive_or_404(new_id)
    return {"item": _row_to_dict(row), "cleared_pending_docs": cleared}


@router.get("")
def list_drawings(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    customer_code: str | None = Query(None),
    drawing_no: str | None = Query(None),
    material: str | None = Query(None),
    spec_model: str | None = Query(None),
    drawing_type: str | None = Query(None),
    drawing_name: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    _ensure_tables()
    filters = {
        "customer_code": (customer_code or "").strip(),
        "drawing_no": (drawing_no or "").strip(),
        "material": (material or "").strip(),
        "spec_model": (spec_model or "").strip(),
        "drawing_type": (drawing_type or "").strip(),
        "drawing_name": (drawing_name or "").strip(),
    }
    if not any(filters.values()):
        raise HTTPException(status_code=400, detail="请至少输入一个查询条件")

    clauses: list[str] = []
    params: list[Any] = []
    for col, val in filters.items():
        if val:
            clauses.append(f"{col} LIKE %s")
            params.append(f"%{val}%")
    where = " AND ".join(clauses)
    params.append(limit)
    rows = fetch_all(
        f"""
        SELECT * FROM drawing_archives
        WHERE {where}
        ORDER BY uploaded_at DESC, id DESC
        LIMIT %s
        """,
        tuple(params),
    )
    return {"items": [_row_to_dict(r) for r in rows], "total": len(rows)}


@router.get("/by-drawing-name")
def get_by_drawing_name(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    drawing_name: str = Query(...),
):
    _ensure_tables()
    name = drawing_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="请输入图纸名")
    # 去掉用户可能误带的 .pdf
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    row = fetch_one("SELECT * FROM drawing_archives WHERE drawing_name = %s", (name,))
    items = [_row_to_dict(row)] if row else []
    return {"items": items, "total": len(items)}


@router.get("/by-order")
def get_by_order(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    order_no: str = Query(...),
):
    _ensure_tables()
    ono = order_no.strip()
    if not ono:
        raise HTTPException(status_code=400, detail="请输入订单号")

    detail_rows = fetch_all(
        """
        SELECT DISTINCT drawing_no
        FROM order_details
        WHERE order_no = %s
          AND drawing_no IS NOT NULL
          AND TRIM(drawing_no) <> ''
        """,
        (ono,),
    )
    drawing_nos = [str(r["drawing_no"]).strip() for r in detail_rows if r.get("drawing_no")]
    if not drawing_nos:
        return {"items": [], "total": 0, "message": "该订单不存在或无图纸号"}

    placeholders = ", ".join(["%s"] * len(drawing_nos))
    rows = fetch_all(
        f"""
        SELECT * FROM drawing_archives
        WHERE drawing_no IN ({placeholders})
        ORDER BY drawing_name ASC, id ASC
        """,
        tuple(drawing_nos),
    )
    return {
        "items": [_row_to_dict(r) for r in rows],
        "total": len(rows),
        "drawing_nos": drawing_nos,
        "message": None if rows else "订单有图纸号，但图纸建档中无匹配记录",
    }


@router.post("/batch-download")
def batch_download(
    body: BatchDownloadRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    _ensure_tables()
    ids = [int(x) for x in body.ids if int(x) > 0]
    if not ids:
        raise HTTPException(status_code=400, detail="请选择要下载的图纸")

    placeholders = ", ".join(["%s"] * len(ids))
    rows = fetch_all(
        f"SELECT * FROM drawing_archives WHERE id IN ({placeholders}) ORDER BY id ASC",
        tuple(ids),
    )
    if len(rows) != len(set(ids)):
        raise HTTPException(status_code=404, detail="部分图纸不存在，请刷新后重试")

    buf = io.BytesIO()
    missing: list[str] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for row in rows:
            path = _absolute_from_relative(str(row["storage_relative_path"]))
            arcname = f"{row['drawing_name']}.pdf"
            if not path.is_file():
                missing.append(str(row["drawing_name"]))
                continue
            zf.write(path, arcname=arcname)
    if missing and len(missing) == len(rows):
        raise HTTPException(status_code=404, detail="所选图纸文件均不存在或已被移动")

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    order_no = (body.order_no or "").strip()
    order_prefix = _safe_segment(order_no, fallback="") if order_no else ""
    if order_prefix:
        zip_name = f"{order_prefix}_dr_{stamp}.zip"
    else:
        zip_name = f"dr_{stamp}.zip"
    _write_log(
        None,
        "batch_download",
        user,
        {
            "ids": [int(r["id"]) for r in rows],
            "drawing_names": [str(r["drawing_name"]) for r in rows],
            "missing_files": missing,
            "order_no": order_no or None,
            "zip_name": zip_name,
        },
    )
    buf.seek(0)
    # header 必须为 latin-1 可编码；中文等非 ASCII 用 RFC5987 百分号编码
    ascii_name = zip_name.encode("ascii", "ignore").decode("ascii") or f"dr_{stamp}.zip"
    headers = {
        "Content-Disposition": (
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(zip_name)}"
        ),
    }
    return Response(content=buf.getvalue(), media_type="application/zip", headers=headers)


@router.get("/{archive_id}/download")
def download_drawing(
    archive_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    _ensure_tables()
    row = _get_archive_or_404(archive_id)
    path = _absolute_from_relative(str(row["storage_relative_path"]))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="图纸文件不存在或已被移动")
    _write_log(
        archive_id,
        "download",
        user,
        {"drawing_name": row["drawing_name"], "path": row["storage_relative_path"]},
    )
    filename = f"{row['drawing_name']}.pdf"
    return FileResponse(
        path=path,
        filename=filename,
        media_type=row.get("content_type") or "application/pdf",
        content_disposition_type="attachment",
    )


@router.delete("/{archive_id}")
def delete_drawing(
    archive_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    _ensure_tables()
    row = _get_archive_or_404(archive_id)
    rel = str(row["storage_relative_path"])
    path = _absolute_from_relative(rel)
    file_deleted = False
    file_missing = False
    if path.is_file():
        path.unlink()
        file_deleted = True
    else:
        file_missing = True

    drawing_no = str(row.get("drawing_no") or "").strip()
    execute("DELETE FROM drawing_archives WHERE id = %s", (archive_id,))
    _write_log(
        archive_id,
        "delete",
        user,
        {
            "drawing_name": row["drawing_name"],
            "path": rel,
            "file_deleted": file_deleted,
            "file_missing_before_delete": file_missing,
            "customer_code": row["customer_code"],
            "drawing_no": row["drawing_no"],
        },
    )
    # 同号图纸已全部删除时，订单行重新标记待补资料
    from app.api.v1.workflow import mark_pending_doc_status_for_drawing_no

    marked = mark_pending_doc_status_for_drawing_no(drawing_no)
    return {"ok": True, "file_missing_before_delete": file_missing, "re_marked_pending_docs": marked}
