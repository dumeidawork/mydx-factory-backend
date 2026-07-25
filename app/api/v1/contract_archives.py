"""客户原始合同上传归档 API。"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.core.database import execute, fetch_all, fetch_one
from app.core.paths import get_contract_archives_dir

router = APIRouter(prefix="/contract-archives", tags=["客户原始合同归档"])

ALLOWED_EXTENSIONS = {".pdf", ".xlsx", ".xls"}
VERSION_RE = re.compile(r"^V?\d+\.\d+\.\d+$", re.IGNORECASE)
_WIN_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class MetaPatchRequest(BaseModel):
    customer_name: str | None = None
    summary: str | None = None
    upload_date: str | None = None
    updated_date: str | None = None
    owner_user_id: int | None = Field(default=None)
    clear_owner: bool = False


class AssignOwnerRequest(BaseModel):
    owner_user_id: int
    archive_ids: list[int] = Field(default_factory=list)


def _parse_date(value: str | date | None, field_name: str) -> date:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise HTTPException(status_code=400, detail=f"{field_name}不能为空")
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip().replace("/", "").replace("-", "")
    if len(text) == 8 and text.isdigit():
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{field_name}格式无效，请使用 yyyymmdd") from exc
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field_name}格式无效，请使用 yyyymmdd") from exc


def _normalize_version(version: str) -> str:
    raw = (version or "").strip() or "V0.0.0"
    if not VERSION_RE.match(raw):
        raise HTTPException(status_code=400, detail="版本号格式须为 V主.次.修订（如 V0.0.0）")
    digits = raw[1:] if raw[0] in "Vv" else raw
    return f"V{digits}"


def _safe_segment(name: str, fallback: str = "unnamed") -> str:
    cleaned = _WIN_INVALID.sub("_", (name or "").strip())
    cleaned = cleaned.rstrip(" .")
    return cleaned[:120] or fallback


def _date_yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


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
        INSERT INTO customer_contract_archive_logs
            (archive_id, action, operator_user_id, operator_name, change_summary)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (archive_id, action, operator.id, operator.name, summary),
    )


def _ensure_unique(customer: str, file_name: str, version: str, upload_date: date, exclude_id: int | None = None) -> None:
    if exclude_id:
        row = fetch_one(
            """
            SELECT id FROM customer_contract_archives
            WHERE customer_name=%s AND file_name=%s AND version=%s AND upload_date=%s AND id<>%s
            """,
            (customer, file_name, version, upload_date, exclude_id),
        )
    else:
        row = fetch_one(
            """
            SELECT id FROM customer_contract_archives
            WHERE customer_name=%s AND file_name=%s AND version=%s AND upload_date=%s
            """,
            (customer, file_name, version, upload_date),
        )
    if row:
        raise HTTPException(
            status_code=400,
            detail="已经上传过该文件了（同一客户名+文件名+版本号+上传日期）。如需更新合同，请上传新版本。",
        )


def _resolve_owner(owner_user_id: int | None) -> tuple[int | None, str | None]:
    if owner_user_id is None:
        return None, None
    row = fetch_one("SELECT id, name FROM users WHERE id = %s", (owner_user_id,))
    if not row:
        raise HTTPException(status_code=400, detail="业务负责人不存在")
    return int(row["id"]), str(row["name"] or "")


def _save_upload_file(
    customer: str,
    version: str,
    upload_date: date,
    upload: UploadFile,
    raw: bytes,
) -> tuple[str, str, str]:
    original_name = Path(upload.filename or "contract.bin").name
    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持 pdf / excel（.pdf .xlsx .xls）")
    stem = Path(original_name).stem
    folder_name = f"{_safe_segment(stem)}_{_safe_segment(version)}_{_date_yyyymmdd(upload_date)}"
    rel_dir = Path(_safe_segment(customer)) / folder_name
    abs_dir = get_contract_archives_dir() / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)
    safe_file = _safe_segment(original_name, fallback=f"contract{ext}")
    abs_path = abs_dir / safe_file
    # 避免同目录覆盖：若已存在则加序号
    if abs_path.exists():
        i = 1
        while True:
            candidate = abs_dir / f"{Path(safe_file).stem}_{i}{ext}"
            if not candidate.exists():
                abs_path = candidate
                safe_file = candidate.name
                break
            i += 1
    abs_path.write_bytes(raw)
    rel_path = str(rel_dir / safe_file).replace("\\", "/")
    content_type = upload.content_type or ""
    return original_name, rel_path, content_type


def _get_archive_or_404(archive_id: int) -> dict:
    row = fetch_one("SELECT * FROM customer_contract_archives WHERE id = %s", (archive_id,))
    if not row:
        raise HTTPException(status_code=404, detail="合同归档不存在")
    return row


def _absolute_from_relative(rel: str) -> Path:
    root = get_contract_archives_dir().resolve()
    target = (root / rel).resolve()
    if not str(target).startswith(str(root)):
        raise HTTPException(status_code=400, detail="非法文件路径")
    return target


@router.post("")
async def create_archive(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    customer_name: str = Form(...),
    version: str = Form("V0.0.0"),
    upload_date: str = Form(...),
    updated_date: str | None = Form(None),
    summary: str | None = Form(None),
    owner_user_id: int | None = Form(None),
    file: UploadFile = File(...),
):
    customer = customer_name.strip()
    if not customer:
        raise HTTPException(status_code=400, detail="客户名必须入力")
    ver = _normalize_version(version)
    up_date = _parse_date(upload_date, "上传时间")
    # 首次上传：更新时间为空
    upd_date = None
    if updated_date and str(updated_date).strip():
        upd_date = _parse_date(updated_date, "更新时间")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件不能为空")
    original_name, rel_path, content_type = _save_upload_file(customer, ver, up_date, file, raw)
    _ensure_unique(customer, original_name, ver, up_date)
    owner_id, owner_name = _resolve_owner(owner_user_id)

    new_id = execute(
        """
        INSERT INTO customer_contract_archives (
            customer_name, file_name, version, upload_date,
            uploaded_by_user_id, uploaded_by_name,
            updated_date, updated_by_user_id, updated_by_name,
            summary, owner_user_id, owner_name,
            storage_relative_path, file_size, content_type
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            customer,
            original_name,
            ver,
            up_date,
            user.id,
            user.name,
            upd_date,
            None if upd_date is None else user.id,
            "" if upd_date is None else user.name,
            (summary or "").strip() or None,
            owner_id,
            owner_name,
            rel_path,
            len(raw),
            content_type,
        ),
    )
    _write_log(
        new_id,
        "create",
        user,
        {
            "customer_name": customer,
            "file_name": original_name,
            "version": ver,
            "upload_date": _date_yyyymmdd(up_date),
            "storage_relative_path": rel_path,
            "owner_user_id": owner_id,
        },
    )
    row = _get_archive_or_404(new_id)
    return {"item": _row_to_dict(row), "owner_assigned": owner_id is not None}


@router.get("")
def list_archives(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    customer_name: str = Query(""),
    file_name: str = Query(""),
    version: str = Query(""),
    upload_date_from: str = Query(""),
    upload_date_to: str = Query(""),
    updated_date_from: str = Query(""),
    updated_date_to: str = Query(""),
    uploaded_by: str = Query(""),
    updated_by: str = Query(""),
    owner_name: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    clauses: list[str] = ["1=1"]
    params: list[Any] = []
    if customer_name.strip():
        clauses.append("customer_name LIKE %s")
        params.append(f"%{customer_name.strip()}%")
    if file_name.strip():
        clauses.append("file_name LIKE %s")
        params.append(f"%{file_name.strip()}%")
    if version.strip():
        clauses.append("version LIKE %s")
        params.append(f"%{version.strip()}%")
    if upload_date_from.strip():
        clauses.append("upload_date >= %s")
        params.append(_parse_date(upload_date_from, "upload_date_from"))
    if upload_date_to.strip():
        clauses.append("upload_date <= %s")
        params.append(_parse_date(upload_date_to, "upload_date_to"))
    if updated_date_from.strip():
        clauses.append("updated_date >= %s")
        params.append(_parse_date(updated_date_from, "updated_date_from"))
    if updated_date_to.strip():
        clauses.append("updated_date <= %s")
        params.append(_parse_date(updated_date_to, "updated_date_to"))
    if uploaded_by.strip():
        clauses.append("uploaded_by_name LIKE %s")
        params.append(f"%{uploaded_by.strip()}%")
    if updated_by.strip():
        clauses.append("updated_by_name LIKE %s")
        params.append(f"%{updated_by.strip()}%")
    if owner_name.strip():
        clauses.append("owner_name LIKE %s")
        params.append(f"%{owner_name.strip()}%")

    where = " AND ".join(clauses)
    total_row = fetch_one(f"SELECT COUNT(*) AS cnt FROM customer_contract_archives WHERE {where}", tuple(params))
    rows = fetch_all(
        f"""
        SELECT * FROM customer_contract_archives
        WHERE {where}
        ORDER BY updated_date DESC, id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [limit, offset]),
    )
    return {"total": int((total_row or {}).get("cnt") or 0), "items": [_row_to_dict(r) for r in rows]}


@router.get("/unassigned")
def list_unassigned(user: Annotated[CurrentUser, Depends(get_current_user)]):
    rows = fetch_all(
        """
        SELECT * FROM customer_contract_archives
        WHERE owner_user_id IS NULL OR owner_user_id = 0 OR owner_name IS NULL OR owner_name = ''
        ORDER BY upload_date DESC, id DESC
        """
    )
    return {"items": [_row_to_dict(r) for r in rows]}


@router.get("/customers/suggest")
def suggest_customers(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    q: str = Query(""),
    limit: int = Query(20, ge=1, le=50),
):
    keyword = q.strip()
    if keyword:
        rows = fetch_all(
            """
            SELECT DISTINCT customer_name FROM customer_contract_archives
            WHERE customer_name LIKE %s
            ORDER BY customer_name
            LIMIT %s
            """,
            (f"%{keyword}%", limit),
        )
    else:
        rows = fetch_all(
            """
            SELECT DISTINCT customer_name FROM customer_contract_archives
            ORDER BY customer_name
            LIMIT %s
            """,
            (limit,),
        )
    # 同时补充订单明细中出现过的客户名
    if keyword:
        order_rows = fetch_all(
            """
            SELECT DISTINCT customer AS customer_name FROM order_details
            WHERE customer IS NOT NULL AND customer <> '' AND customer LIKE %s
            ORDER BY customer
            LIMIT %s
            """,
            (f"%{keyword}%", limit),
        )
    else:
        order_rows = fetch_all(
            """
            SELECT DISTINCT customer AS customer_name FROM order_details
            WHERE customer IS NOT NULL AND customer <> ''
            ORDER BY customer
            LIMIT %s
            """,
            (limit,),
        )
    names: list[str] = []
    seen: set[str] = set()
    for r in rows + order_rows:
        name = str(r.get("customer_name") or "").strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return {"items": names[:limit]}


@router.patch("/{archive_id}")
def patch_archive(
    archive_id: int,
    body: MetaPatchRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    row = _get_archive_or_404(archive_id)
    changes: dict[str, Any] = {}
    customer = row["customer_name"]
    upload_date = row["upload_date"]
    if isinstance(upload_date, datetime):
        upload_date = upload_date.date()
    elif isinstance(upload_date, str):
        upload_date = _parse_date(upload_date, "upload_date")

    if body.customer_name is not None:
        new_customer = body.customer_name.strip()
        if not new_customer:
            raise HTTPException(status_code=400, detail="客户名不能为空")
        if new_customer != row["customer_name"]:
            changes["customer_name"] = {"from": row["customer_name"], "to": new_customer}
            customer = new_customer

    if body.upload_date is not None:
        new_up = _parse_date(body.upload_date, "上传时间")
        if new_up != upload_date:
            changes["upload_date"] = {"from": _date_yyyymmdd(upload_date), "to": _date_yyyymmdd(new_up)}
            upload_date = new_up

    if body.customer_name is not None or body.upload_date is not None:
        _ensure_unique(customer, row["file_name"], row["version"], upload_date, exclude_id=archive_id)

    summary = row.get("summary")
    if body.summary is not None:
        new_summary = body.summary.strip() or None
        if new_summary != row.get("summary"):
            changes["summary"] = {"from": row.get("summary"), "to": new_summary}
            summary = new_summary

    updated_date = row.get("updated_date")
    if isinstance(updated_date, datetime):
        updated_date = updated_date.date()
    elif isinstance(updated_date, str) and updated_date.strip():
        updated_date = _parse_date(updated_date, "updated_date")
    elif not updated_date:
        updated_date = None
    if body.updated_date is not None:
        if str(body.updated_date).strip() == "":
            if updated_date is not None:
                changes["updated_date"] = {
                    "from": _date_yyyymmdd(updated_date) if updated_date else None,
                    "to": None,
                }
            updated_date = None
        else:
            new_upd = _parse_date(body.updated_date, "更新时间")
            if new_upd != updated_date:
                changes["updated_date"] = {
                    "from": _date_yyyymmdd(updated_date) if updated_date else None,
                    "to": _date_yyyymmdd(new_upd),
                }
                updated_date = new_upd

    owner_id = row.get("owner_user_id")
    owner_name = row.get("owner_name")
    if body.clear_owner:
        if owner_id or owner_name:
            changes["owner"] = {"from": {"id": owner_id, "name": owner_name}, "to": None}
        owner_id, owner_name = None, None
    elif body.owner_user_id is not None:
        new_owner_id, new_owner_name = _resolve_owner(body.owner_user_id)
        if new_owner_id != owner_id:
            changes["owner"] = {
                "from": {"id": owner_id, "name": owner_name},
                "to": {"id": new_owner_id, "name": new_owner_name},
            }
            owner_id, owner_name = new_owner_id, new_owner_name

    if not changes:
        return {"item": _row_to_dict(row), "changed": False}

    execute(
        """
        UPDATE customer_contract_archives SET
            customer_name=%s, upload_date=%s, summary=%s, updated_date=%s,
            updated_by_user_id=%s, updated_by_name=%s,
            owner_user_id=%s, owner_name=%s
        WHERE id=%s
        """,
        (
            customer,
            upload_date,
            summary,
            updated_date,
            user.id,
            user.name,
            owner_id,
            owner_name,
            archive_id,
        ),
    )
    action = "assign_owner" if "owner" in changes and len(changes) == 1 else "update_meta"
    _write_log(archive_id, action, user, changes)
    return {"item": _row_to_dict(_get_archive_or_404(archive_id)), "changed": True}


@router.post("/assign-owner")
def assign_owner_batch(
    body: AssignOwnerRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    if not body.archive_ids:
        raise HTTPException(status_code=400, detail="请选择合同")
    owner_id, owner_name = _resolve_owner(body.owner_user_id)
    updated = 0
    for aid in body.archive_ids:
        row = fetch_one("SELECT id, owner_user_id, owner_name FROM customer_contract_archives WHERE id=%s", (aid,))
        if not row:
            continue
        execute(
            """
            UPDATE customer_contract_archives
            SET owner_user_id=%s, owner_name=%s, updated_by_user_id=%s, updated_by_name=%s
            WHERE id=%s
            """,
            (owner_id, owner_name, user.id, user.name, aid),
        )
        _write_log(
            aid,
            "assign_owner",
            user,
            {"from": {"id": row.get("owner_user_id"), "name": row.get("owner_name")}, "to": {"id": owner_id, "name": owner_name}},
        )
        updated += 1
    return {"updated": updated, "owner_user_id": owner_id, "owner_name": owner_name}


@router.post("/{archive_id}/versions")
async def create_version(
    archive_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    version: str = Form(...),
    updated_date: str = Form(...),
    summary: str | None = Form(None),
    file: UploadFile = File(...),
):
    base = _get_archive_or_404(archive_id)
    ver = _normalize_version(version)
    upd_date = _parse_date(updated_date, "更新时间")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件不能为空")

    upload_date = base["upload_date"]
    if isinstance(upload_date, datetime):
        upload_date = upload_date.date()
    elif isinstance(upload_date, str):
        upload_date = _parse_date(upload_date, "upload_date")

    original_name, rel_path, content_type = _save_upload_file(base["customer_name"], ver, upload_date, file, raw)
    # 版本更新：文件名以新文件为准，但仍与原上传日期组合唯一
    _ensure_unique(base["customer_name"], original_name, ver, upload_date)

    new_summary = (summary if summary is not None else base.get("summary")) or None
    if isinstance(new_summary, str):
        new_summary = new_summary.strip() or None

    new_id = execute(
        """
        INSERT INTO customer_contract_archives (
            customer_name, file_name, version, upload_date,
            uploaded_by_user_id, uploaded_by_name,
            updated_date, updated_by_user_id, updated_by_name,
            summary, owner_user_id, owner_name,
            storage_relative_path, file_size, content_type
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            base["customer_name"],
            original_name,
            ver,
            upload_date,
            base.get("uploaded_by_user_id"),
            base.get("uploaded_by_name") or "",
            upd_date,
            user.id,
            user.name,
            new_summary,
            base.get("owner_user_id"),
            base.get("owner_name"),
            rel_path,
            len(raw),
            content_type,
        ),
    )
    _write_log(
        new_id,
        "version_update",
        user,
        {
            "from_archive_id": archive_id,
            "from_version": base.get("version"),
            "to_version": ver,
            "file_name": original_name,
            "updated_date": _date_yyyymmdd(upd_date),
        },
    )
    return {"item": _row_to_dict(_get_archive_or_404(new_id))}


@router.get("/{archive_id}/download")
def download_archive(
    archive_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    row = _get_archive_or_404(archive_id)
    path = _absolute_from_relative(row["storage_relative_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="归档文件不存在或已被移动")
    _write_log(archive_id, "download", user, {"file_name": row["file_name"], "path": row["storage_relative_path"]})
    media = row.get("content_type") or ""
    if not media:
        ext = Path(str(row.get("file_name") or "")).suffix.lower()
        media = {
            ".pdf": "application/pdf",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".xls": "application/vnd.ms-excel",
        }.get(ext, "application/octet-stream")
    return FileResponse(
        path=path,
        filename=str(row["file_name"]),
        media_type=media,
        content_disposition_type="attachment",
    )


def bump_version(version: str) -> str:
    """末位 +1，供前端参考；服务端版本更新接口接受客户端提交值。"""
    ver = _normalize_version(version)
    parts = ver[1:].split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return "V" + ".".join(parts)
