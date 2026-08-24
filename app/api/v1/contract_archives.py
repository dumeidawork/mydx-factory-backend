"""客户原始合同上传归档 API。"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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

PROGRESS_UPLOAD = "上传"
PROGRESS_ASSIGNED = "分配"
PROGRESS_CONFIRMED = "确认"
PROGRESS_MODIFIED = "修改"
AMOUNT_MISMATCH_DETAIL = "金额不一致，请确认真实合同金额"

_progress_columns_ready = False


class MetaPatchRequest(BaseModel):
    customer_name: str | None = None
    summary: str | None = None
    upload_date: str | None = None
    updated_date: str | None = None
    owner_user_id: int | None = Field(default=None)
    clear_owner: bool = False
    total_amount: Decimal | None = None


class AssignOwnerRequest(BaseModel):
    owner_user_id: int
    archive_ids: list[int] = Field(default_factory=list)


class ConfirmTakeoverRequest(BaseModel):
    total_amount: Decimal
    confirm_real_amount: bool = False


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
        if isinstance(v, Decimal):
            return float(v)
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
    _ensure_progress_columns()
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


def _column_exists(table: str, column: str) -> bool:
    row = fetch_one(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s
        LIMIT 1
        """,
        (table, column),
    )
    return bool(row)


def _index_exists(table: str, index_name: str) -> bool:
    row = fetch_one(
        """
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = %s AND index_name = %s
        LIMIT 1
        """,
        (table, index_name),
    )
    return bool(row)


def _ensure_progress_columns() -> None:
    """启动后首次访问时补列、索引，并回填历史进度。"""
    global _progress_columns_ready
    if _progress_columns_ready:
        return
    if not _column_exists("customer_contract_archives", "progress_status"):
        execute(
            """
            ALTER TABLE customer_contract_archives
            ADD COLUMN progress_status VARCHAR(16) NOT NULL DEFAULT '上传'
                COMMENT '合同进度：上传/分配/确认' AFTER owner_name
            """
        )
    if not _column_exists("customer_contract_archives", "assigned_at"):
        execute(
            """
            ALTER TABLE customer_contract_archives
            ADD COLUMN assigned_at DATETIME NULL COMMENT '进入本次分配的时间' AFTER progress_status
            """
        )
    if not _column_exists("customer_contract_archives", "total_amount"):
        execute(
            """
            ALTER TABLE customer_contract_archives
            ADD COLUMN total_amount DECIMAL(14,2) NULL COMMENT '合同总金额' AFTER assigned_at
            """
        )
    if not _column_exists("customer_contract_archives", "previous_total_amount"):
        execute(
            """
            ALTER TABLE customer_contract_archives
            ADD COLUMN previous_total_amount DECIMAL(14,2) NULL COMMENT '修改前合同总金额' AFTER total_amount
            """
        )
    if not _index_exists("customer_contract_archives", "idx_archive_owner_progress"):
        execute(
            """
            ALTER TABLE customer_contract_archives
            ADD INDEX idx_archive_owner_progress (owner_user_id, progress_status)
            """
        )
    if not _index_exists("customer_contract_archives", "idx_archive_uploader_progress"):
        execute(
            """
            ALTER TABLE customer_contract_archives
            ADD INDEX idx_archive_uploader_progress (uploaded_by_user_id, progress_status)
            """
        )
    execute(
        """
        UPDATE customer_contract_archives
        SET progress_status = %s
        WHERE owner_user_id IS NOT NULL AND owner_user_id <> 0
          AND IFNULL(progress_status, %s) = %s
        """,
        (PROGRESS_ASSIGNED, PROGRESS_UPLOAD, PROGRESS_UPLOAD),
    )
    pending = fetch_all(
        """
        SELECT id, created_at FROM customer_contract_archives
        WHERE progress_status = %s AND assigned_at IS NULL
          AND owner_user_id IS NOT NULL AND owner_user_id <> 0
        """,
        (PROGRESS_ASSIGNED,),
    )
    if pending:
        log_rows = fetch_all(
            """
            SELECT archive_id, MAX(operated_at) AS last_assign
            FROM customer_contract_archive_logs
            WHERE action = 'assign_owner'
            GROUP BY archive_id
            """
        )
        last_assign = {
            int(r["archive_id"]): r["last_assign"]
            for r in log_rows
            if r.get("archive_id") is not None
        }
        for row in pending:
            aid = int(row["id"])
            execute(
                "UPDATE customer_contract_archives SET assigned_at=%s WHERE id=%s AND assigned_at IS NULL",
                (last_assign.get(aid) or row.get("created_at"), aid),
            )
    _progress_columns_ready = True


def _normalize_owner_id(value: Any) -> int | None:
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


CHAIRMAN_ROLE_CODE = "super_admin"


def _is_chairman(user: CurrentUser) -> bool:
    return user.role_code == CHAIRMAN_ROLE_CODE


def _visibility_sql(user: CurrentUser) -> tuple[str, list[Any]]:
    """董事长可看全部；其他人仅看上传者/更新者/业务负责人为自己的合同。"""
    if _is_chairman(user):
        return "1=1", []
    return (
        "(uploaded_by_user_id=%s OR updated_by_user_id=%s OR owner_user_id=%s)",
        [user.id, user.id, user.id],
    )


def _archive_visible_to(row: dict, user: CurrentUser) -> bool:
    if _is_chairman(user):
        return True
    uid = user.id
    return uid in {
        _normalize_owner_id(row.get("uploaded_by_user_id")),
        _normalize_owner_id(row.get("updated_by_user_id")),
        _normalize_owner_id(row.get("owner_user_id")),
    }


def _assert_archive_visible(row: dict, user: CurrentUser) -> None:
    if not _archive_visible_to(row, user):
        raise HTTPException(status_code=404, detail="合同归档不存在")


def _normalize_amount(value: Any, *, required: bool) -> Decimal | None:
    if value is None or (isinstance(value, str) and not str(value).strip()):
        if required:
            raise HTTPException(status_code=400, detail="合同总金额不能为空")
        return None
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=400, detail="合同总金额格式无效") from exc
    if amount < 0:
        raise HTTPException(status_code=400, detail="合同总金额不能为负数")
    return amount


def _amounts_equal(stored: Any, entered: Decimal) -> bool:
    if stored is None or (isinstance(stored, str) and not str(stored).strip()):
        return True
    try:
        current = Decimal(str(stored)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return True
    return current == entered


def _has_downloaded_after_assign(archive_id: int, user_id: int, assigned_at: Any) -> bool:
    if not assigned_at:
        return False
    row = fetch_one(
        """
        SELECT 1 FROM customer_contract_archive_logs
        WHERE archive_id=%s AND action='download' AND operator_user_id=%s AND operated_at >= %s
        LIMIT 1
        """,
        (archive_id, user_id, assigned_at),
    )
    return bool(row)


def _downloaded_after_assign_map(rows: list[dict], user_id: int) -> dict[int, bool]:
    assigned: dict[int, Any] = {}
    for row in rows:
        at = row.get("assigned_at")
        if at:
            assigned[int(row["id"])] = at
    result = {int(row["id"]): False for row in rows}
    if not assigned:
        return result
    ids = list(assigned)
    placeholders = ", ".join(["%s"] * len(ids))
    logs = fetch_all(
        f"""
        SELECT archive_id, MAX(operated_at) AS last_download
        FROM customer_contract_archive_logs
        WHERE action='download' AND operator_user_id=%s AND archive_id IN ({placeholders})
        GROUP BY archive_id
        """,
        (user_id, *ids),
    )
    for log in logs:
        aid = int(log["archive_id"])
        last = log.get("last_download")
        at = assigned.get(aid)
        result[aid] = bool(last and at and last >= at)
    return result


def _pending_item_dict(row: dict, downloaded: bool) -> dict:
    item = _row_to_dict(row)
    item.pop("total_amount", None)
    item.pop("previous_total_amount", None)
    item["downloaded_after_assign"] = downloaded
    return item


def get_pending_takeover_items(user_id: int) -> list[dict]:
    _ensure_progress_columns()
    rows = fetch_all(
        """
        SELECT * FROM customer_contract_archives
        WHERE owner_user_id = %s AND progress_status = %s
        ORDER BY assigned_at DESC, id DESC
        """,
        (user_id, PROGRESS_ASSIGNED),
    )
    downloaded = _downloaded_after_assign_map(rows, user_id)
    return [_pending_item_dict(row, downloaded.get(int(row["id"]), False)) for row in rows]


def get_pending_amount_confirm_items(user_id: int) -> list[dict]:
    _ensure_progress_columns()
    rows = fetch_all(
        """
        SELECT * FROM customer_contract_archives
        WHERE uploaded_by_user_id = %s AND progress_status = %s
        ORDER BY updated_at DESC, id DESC
        """,
        (user_id, PROGRESS_MODIFIED),
    )
    return [_row_to_dict(r) for r in rows]


@router.post("")
async def create_archive(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    customer_name: str = Form(...),
    version: str = Form("V0.0.0"),
    upload_date: str = Form(...),
    updated_date: str | None = Form(None),
    summary: str | None = Form(None),
    owner_user_id: int | None = Form(None),
    total_amount: str | None = Form(None),
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
    _ensure_progress_columns()
    amount = _normalize_amount(total_amount, required=True)
    progress_status = PROGRESS_ASSIGNED if owner_id else PROGRESS_UPLOAD

    new_id = execute(
        """
        INSERT INTO customer_contract_archives (
            customer_name, file_name, version, upload_date,
            uploaded_by_user_id, uploaded_by_name,
            updated_date, updated_by_user_id, updated_by_name,
            summary, owner_user_id, owner_name, progress_status, assigned_at,
            total_amount, previous_total_amount,
            storage_relative_path, file_size, content_type
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,IF(%s, NOW(), NULL),%s,NULL,%s,%s,%s)
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
            progress_status,
            1 if owner_id else 0,
            amount,
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
            "total_amount": str(amount),
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
    _ensure_progress_columns()
    vis_sql, vis_params = _visibility_sql(user)
    clauses: list[str] = [vis_sql]
    params: list[Any] = list(vis_params)
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
    _ensure_progress_columns()
    vis_sql, vis_params = _visibility_sql(user)
    rows = fetch_all(
        f"""
        SELECT * FROM customer_contract_archives
        WHERE (owner_user_id IS NULL OR owner_user_id = 0 OR owner_name IS NULL OR owner_name = '')
          AND ({vis_sql})
        ORDER BY upload_date DESC, id DESC
        """,
        tuple(vis_params),
    )
    return {"items": [_row_to_dict(r) for r in rows]}


@router.get("/pending-takeover")
def list_pending_takeover(user: Annotated[CurrentUser, Depends(get_current_user)]):
    return {"items": get_pending_takeover_items(user.id)}


@router.get("/pending-amount-confirm")
def list_pending_amount_confirm(user: Annotated[CurrentUser, Depends(get_current_user)]):
    return {"items": get_pending_amount_confirm_items(user.id)}


@router.get("/customers/suggest")
def suggest_customers(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    q: str = Query(""),
    limit: int = Query(20, ge=1, le=50),
):
    keyword = q.strip()
    vis_sql, vis_params = _visibility_sql(user)
    if keyword:
        rows = fetch_all(
            f"""
            SELECT DISTINCT customer_name FROM customer_contract_archives
            WHERE customer_name LIKE %s AND ({vis_sql})
            ORDER BY customer_name
            LIMIT %s
            """,
            tuple([f"%{keyword}%", *vis_params, limit]),
        )
    else:
        rows = fetch_all(
            f"""
            SELECT DISTINCT customer_name FROM customer_contract_archives
            WHERE {vis_sql}
            ORDER BY customer_name
            LIMIT %s
            """,
            tuple([*vis_params, limit]),
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
    _ensure_progress_columns()
    row = _get_archive_or_404(archive_id)
    _assert_archive_visible(row, user)
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

    total_amount = row.get("total_amount")
    if body.total_amount is not None:
        new_amount = _normalize_amount(body.total_amount, required=True)
        old_amount = _normalize_amount(total_amount, required=False)
        if old_amount != new_amount:
            changes["total_amount"] = {
                "from": str(old_amount) if old_amount is not None else None,
                "to": str(new_amount),
            }
            total_amount = new_amount

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
    progress_status = str(row.get("progress_status") or PROGRESS_UPLOAD)
    assigned_at_mode = "keep"  # keep | now | null
    if body.clear_owner:
        if owner_id or owner_name:
            changes["owner"] = {"from": {"id": owner_id, "name": owner_name}, "to": None}
            progress_status = PROGRESS_UPLOAD
            assigned_at_mode = "null"
        owner_id, owner_name = None, None
    elif body.owner_user_id is not None:
        new_owner_id, new_owner_name = _resolve_owner(body.owner_user_id)
        if _normalize_owner_id(new_owner_id) != _normalize_owner_id(owner_id):
            changes["owner"] = {
                "from": {"id": owner_id, "name": owner_name},
                "to": {"id": new_owner_id, "name": new_owner_name},
            }
            owner_id, owner_name = new_owner_id, new_owner_name
            progress_status = PROGRESS_ASSIGNED
            assigned_at_mode = "now"

    if not changes:
        return {"item": _row_to_dict(row), "changed": False}

    if assigned_at_mode == "null":
        assigned_sql = "NULL"
        previous_sql = "NULL"
    elif assigned_at_mode == "now":
        assigned_sql = "NOW()"
        previous_sql = "NULL"
    else:
        assigned_sql = "assigned_at"
        previous_sql = "previous_total_amount"

    execute(
        f"""
        UPDATE customer_contract_archives SET
            customer_name=%s, upload_date=%s, summary=%s, updated_date=%s,
            updated_by_user_id=%s, updated_by_name=%s,
            owner_user_id=%s, owner_name=%s,
            progress_status=%s, assigned_at={assigned_sql},
            total_amount=%s, previous_total_amount={previous_sql}
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
            progress_status,
            total_amount,
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
    _ensure_progress_columns()
    owner_id, owner_name = _resolve_owner(body.owner_user_id)
    updated = 0
    for aid in body.archive_ids:
        row = fetch_one(
            """
            SELECT id, owner_user_id, owner_name, progress_status,
                   uploaded_by_user_id, updated_by_user_id
            FROM customer_contract_archives WHERE id=%s
            """,
            (aid,),
        )
        if not row or not _archive_visible_to(row, user):
            continue
        owner_changed = _normalize_owner_id(row.get("owner_user_id")) != _normalize_owner_id(owner_id)
        if owner_changed:
            execute(
                """
                UPDATE customer_contract_archives
                SET owner_user_id=%s, owner_name=%s, progress_status=%s, assigned_at=NOW(),
                    previous_total_amount=NULL, updated_by_user_id=%s, updated_by_name=%s
                WHERE id=%s
                """,
                (owner_id, owner_name, PROGRESS_ASSIGNED, user.id, user.name, aid),
            )
        else:
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
            {
                "from": {"id": row.get("owner_user_id"), "name": row.get("owner_name")},
                "to": {"id": owner_id, "name": owner_name},
                "progress_reset": owner_changed,
            },
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
    total_amount: str | None = Form(None),
    file: UploadFile = File(...),
):
    _ensure_progress_columns()
    base = _get_archive_or_404(archive_id)
    _assert_archive_visible(base, user)
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

    amount = _normalize_amount(total_amount, required=False)
    if amount is None:
        amount = _normalize_amount(base.get("total_amount"), required=False)

    base_owner_id = _normalize_owner_id(base.get("owner_user_id"))
    base_status = str(base.get("progress_status") or "")
    if not base_owner_id:
        progress_status = PROGRESS_UPLOAD
        assigned_sql = "NULL"
    elif base_status == PROGRESS_CONFIRMED:
        progress_status = PROGRESS_CONFIRMED
        assigned_sql = "NULL"
    else:
        progress_status = PROGRESS_ASSIGNED
        assigned_sql = "NOW()"

    new_id = execute(
        f"""
        INSERT INTO customer_contract_archives (
            customer_name, file_name, version, upload_date,
            uploaded_by_user_id, uploaded_by_name,
            updated_date, updated_by_user_id, updated_by_name,
            summary, owner_user_id, owner_name, progress_status, assigned_at,
            total_amount, previous_total_amount,
            storage_relative_path, file_size, content_type
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,{assigned_sql},%s,NULL,%s,%s,%s)
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
            progress_status,
            amount,
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


@router.post("/{archive_id}/confirm-takeover")
def confirm_takeover(
    archive_id: int,
    body: ConfirmTakeoverRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    _ensure_progress_columns()
    row = _get_archive_or_404(archive_id)
    owner_id = _normalize_owner_id(row.get("owner_user_id"))
    if owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅被指定的业务负责人可以确认接手")
    if str(row.get("progress_status") or "") != PROGRESS_ASSIGNED:
        raise HTTPException(status_code=400, detail="当前合同不是待接手状态")
    if not _has_downloaded_after_assign(archive_id, user.id, row.get("assigned_at")):
        raise HTTPException(status_code=400, detail="请先下载该合同的原始文件后再确认接手")
    entered = _normalize_amount(body.total_amount, required=True)
    if entered is None:
        raise HTTPException(status_code=400, detail="合同总金额不能为空")
    stored = row.get("total_amount")
    matched = _amounts_equal(stored, entered)
    if not matched and not body.confirm_real_amount:
        raise HTTPException(status_code=400, detail=AMOUNT_MISMATCH_DETAIL)

    uploader_id = _normalize_owner_id(row.get("uploaded_by_user_id"))
    same_person = uploader_id == user.id
    if matched or same_person:
        next_status = PROGRESS_CONFIRMED
        previous_amount = None
        action = "confirm_takeover"
    else:
        next_status = PROGRESS_MODIFIED
        previous_amount = stored
        action = "confirm_takeover_amount_mismatch"

    execute(
        """
        UPDATE customer_contract_archives
        SET progress_status=%s, total_amount=%s, previous_total_amount=%s
        WHERE id=%s
        """,
        (next_status, entered, previous_amount, archive_id),
    )
    _write_log(
        archive_id,
        action,
        user,
        {
            "from": PROGRESS_ASSIGNED,
            "to": next_status,
            "file_name": row.get("file_name"),
            "entered_amount": str(entered),
            "stored_amount": str(stored) if stored is not None else None,
        },
    )
    return {"item": _row_to_dict(_get_archive_or_404(archive_id))}


@router.post("/{archive_id}/confirm-amount-change")
def confirm_amount_change(
    archive_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    _ensure_progress_columns()
    row = _get_archive_or_404(archive_id)
    uploader_id = _normalize_owner_id(row.get("uploaded_by_user_id"))
    if uploader_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅合同上传者可以确认金额修改")
    if str(row.get("progress_status") or "") != PROGRESS_MODIFIED:
        raise HTTPException(status_code=400, detail="当前合同不是金额修改待确认状态")
    execute(
        """
        UPDATE customer_contract_archives
        SET progress_status=%s, previous_total_amount=NULL
        WHERE id=%s
        """,
        (PROGRESS_CONFIRMED, archive_id),
    )
    _write_log(
        archive_id,
        "confirm_amount_change",
        user,
        {
            "from": PROGRESS_MODIFIED,
            "to": PROGRESS_CONFIRMED,
            "total_amount": str(row.get("total_amount")) if row.get("total_amount") is not None else None,
            "previous_total_amount": str(row.get("previous_total_amount"))
            if row.get("previous_total_amount") is not None
            else None,
        },
    )
    return {"item": _row_to_dict(_get_archive_or_404(archive_id))}


@router.get("/{archive_id}/download")
def download_archive(
    archive_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    row = _get_archive_or_404(archive_id)
    _assert_archive_visible(row, user)
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
