"""仓库体验优化：字典、设置、收据年月归档、列补齐。"""
from __future__ import annotations

import re
import shutil
import zipfile
from datetime import date
from pathlib import Path

from fastapi import HTTPException

from app.api.deps import CurrentUser
from app.core.database import execute, fetch_all, fetch_one
from app.core.paths import get_warehouse_dir
from app.services.warehouse_audit import audit_tuple, update_audit_tuple, write_op_log
from app.services.warehouse_gap import _safe_filename, ensure_gap_tables

DICT_SEEDS = {
    "category": ["五金", "劳保", "刀具", "包装消耗品", "生产消耗品"],
    "purpose": ["还", "1换1", "检验", "维修", "其他"],
    "department": ["机加一车间", "机加二车间", "1号锤", "3号锤", "下料车间", "包装车间", "化验室", "办公室", "其他"],
}

_UX_READY = False


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


def ensure_ux_schema() -> None:
    global _UX_READY
    if _UX_READY:
        return
    ensure_gap_tables()
    execute(
        """
        CREATE TABLE IF NOT EXISTS wh_dict_options (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          kind VARCHAR(32) NOT NULL,
          value VARCHAR(128) NOT NULL,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_wh_dict (kind, value)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='仓库可录入下拉选项'
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS wh_aux_settings (
          setting_key VARCHAR(64) PRIMARY KEY,
          setting_value VARCHAR(255) NOT NULL DEFAULT '',
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='辅料仓键值设置'
        """
    )
    alters = [
        ("wh_inbound_orders", "status", "ALTER TABLE wh_inbound_orders ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'posted'"),
        ("wh_issue_orders", "status", "ALTER TABLE wh_issue_orders ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'posted'"),
        ("wh_attachments", "archived_ym", "ALTER TABLE wh_attachments ADD COLUMN archived_ym VARCHAR(6) NULL"),
        ("wh_supplier_month_invoices", "diff_note", "ALTER TABLE wh_supplier_month_invoices ADD COLUMN diff_note VARCHAR(500) NULL"),
        ("wh_cash_ledger", "source", "ALTER TABLE wh_cash_ledger ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT 'manual'"),
        ("wh_cash_ledger", "status", "ALTER TABLE wh_cash_ledger ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'posted'"),
    ]
    for table, col, ddl in alters:
        if not _column_exists(table, col):
            execute(ddl)
    execute(
        """
        UPDATE wh_cash_ledger SET source='purchase_pay'
        WHERE (source IS NULL OR source='' OR source='manual') AND remark LIKE %s
        """,
        ("现金采购付款%",),
    )
    history_seeds = [
        ("category", "SELECT DISTINCT category AS v FROM wh_aux_items WHERE category IS NOT NULL AND category<>''"),
        ("department", "SELECT DISTINCT department AS v FROM wh_borrow_records WHERE department IS NOT NULL AND department<>''"),
        ("purpose", "SELECT DISTINCT purpose AS v FROM wh_borrow_records WHERE purpose IS NOT NULL AND purpose<>''"),
    ]
    for kind, values in DICT_SEEDS.items():
        for val in values:
            exists = fetch_one("SELECT id FROM wh_dict_options WHERE kind=%s AND value=%s", (kind, val))
            if not exists:
                execute(
                    "INSERT INTO wh_dict_options (kind, value, created_by_name, updated_by_name) VALUES (%s,%s,'system','system')",
                    (kind, val),
                )
    for kind, sql in history_seeds:
        for row in fetch_all(sql):
            val = str(row.get("v") or "").strip()
            if not val:
                continue
            exists = fetch_one("SELECT id FROM wh_dict_options WHERE kind=%s AND value=%s", (kind, val))
            if not exists:
                execute(
                    "INSERT INTO wh_dict_options (kind, value, created_by_name, updated_by_name) VALUES (%s,%s,'system','system')",
                    (kind, val),
                )
    (get_warehouse_dir() / "receipts").mkdir(parents=True, exist_ok=True)
    (get_warehouse_dir() / "receipts_archives").mkdir(parents=True, exist_ok=True)
    _UX_READY = True


def list_dict_options(kind: str) -> list[str]:
    ensure_ux_schema()
    extra: list[str] = []
    if kind == "category":
        extra = [str(r["v"]) for r in fetch_all("SELECT DISTINCT category AS v FROM wh_aux_items WHERE category IS NOT NULL AND category<>''")]
    elif kind == "department":
        extra = [str(r["v"]) for r in fetch_all("SELECT DISTINCT department AS v FROM wh_borrow_records WHERE department IS NOT NULL AND department<>''")]
    elif kind == "purpose":
        extra = [str(r["v"]) for r in fetch_all("SELECT DISTINCT purpose AS v FROM wh_borrow_records WHERE purpose IS NOT NULL AND purpose<>''")]
    rows = fetch_all("SELECT value FROM wh_dict_options WHERE kind=%s ORDER BY id", (kind,))
    seen: set[str] = set()
    out: list[str] = []
    for v in [str(r["value"]) for r in rows] + extra:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def add_dict_option(kind: str, value: str, user: CurrentUser) -> str:
    ensure_ux_schema()
    val = (value or "").strip()
    if not val:
        raise HTTPException(status_code=400, detail="选项不能为空")
    if kind not in ("category", "department", "purpose"):
        raise HTTPException(status_code=400, detail="不支持的字典类型")
    exists = fetch_one("SELECT id FROM wh_dict_options WHERE kind=%s AND value=%s", (kind, val))
    if not exists:
        c_uid, c_name, u_uid, u_name = audit_tuple(user)
        execute(
            """
            INSERT INTO wh_dict_options (kind, value, created_by_user_id, created_by_name, updated_by_user_id, updated_by_name)
            VALUES (%s,%s,%s,%s,%s,%s)
            """,
            (kind, val, c_uid, c_name, u_uid, u_name),
        )
    return val


def get_setting(key: str, default: str = "") -> str:
    ensure_ux_schema()
    row = fetch_one("SELECT setting_value FROM wh_aux_settings WHERE setting_key=%s", (key,))
    return str(row["setting_value"]) if row else default


def set_setting(key: str, value: str, user: CurrentUser) -> None:
    ensure_ux_schema()
    c_uid, c_name, u_uid, u_name = audit_tuple(user)
    execute(
        """
        INSERT INTO wh_aux_settings (setting_key, setting_value, created_by_user_id, created_by_name, updated_by_user_id, updated_by_name)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE setting_value=%s, updated_by_user_id=%s, updated_by_name=%s
        """,
        (key, value, c_uid, c_name, u_uid, u_name, value, u_uid, u_name),
    )


def refresh_cash_warn(user: CurrentUser) -> None:
    ensure_ux_schema()
    raw = get_setting("cash_warn_amount", "")
    try:
        thresh = float(raw) if raw != "" else None
    except ValueError:
        thresh = None
    latest = fetch_one("SELECT next_balance FROM wh_cash_ledger ORDER BY id DESC LIMIT 1")
    bal = float(latest["next_balance"]) if latest else 0.0
    u_uid, u_name = update_audit_tuple(user)
    execute(
        """
        UPDATE wh_alerts SET is_resolved=1, updated_by_user_id=%s, updated_by_name=%s
        WHERE domain='aux' AND alert_type='cash_low' AND is_resolved=0
        """,
        (u_uid, u_name),
    )
    if thresh is None:
        return
    if bal < thresh:
        title = f"现金备用金 {bal:.2f} 元低于预警金额 {thresh:.2f} 元（可能垫付）"
        c_uid, c_name, uu, un = audit_tuple(user)
        execute(
            """
            INSERT INTO wh_alerts
              (domain, alert_type, title, level, ref_type, is_resolved,
               created_by_user_id, created_by_name, updated_by_user_id, updated_by_name)
            VALUES ('aux','cash_low',%s,'warning','cash_ledger',0,%s,%s,%s,%s)
            """,
            (title, c_uid, c_name, uu, un),
        )


def receipts_root() -> Path:
    path = get_warehouse_dir() / "receipts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def receipts_archives_root() -> Path:
    path = get_warehouse_dir() / "receipts_archives"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _two_months_ago_ym(today: date | None = None) -> str:
    d = today or date.today()
    y, m = d.year, d.month - 2
    while m <= 0:
        m += 12
        y -= 1
    return f"{y}{m:02d}"


def maybe_archive_old_receipts() -> str | None:
    """每月 15 日起把上上月 receipts/YYYYMM 打成 zip 并删原目录。"""
    ensure_ux_schema()
    today = date.today()
    if today.day < 15:
        return None
    ym = _two_months_ago_ym(today)
    folder = receipts_root() / ym
    zip_path = receipts_archives_root() / f"{ym}.zip"
    if zip_path.exists():
        execute("UPDATE wh_attachments SET archived_ym=%s WHERE storage_relative_path LIKE %s AND (archived_ym IS NULL OR archived_ym='')", (ym, f"receipts/{ym}/%"))
        return ym
    if not folder.is_dir():
        return None
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in folder.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(folder).as_posix())
    shutil.rmtree(folder, ignore_errors=True)
    execute("UPDATE wh_attachments SET archived_ym=%s WHERE storage_relative_path LIKE %s", (ym, f"receipts/{ym}/%"))
    return ym


def receipt_display_name(po: dict, ext: str) -> str:
    biz = po.get("biz_date")
    day = biz.strftime("%Y%m%d") if hasattr(biz, "strftime") else str(biz or date.today()).replace("-", "")[:8]
    applicant = _safe_filename(str(po.get("purchaser_name") or "申请人"))
    summary = _safe_filename(str(po.get("summary") or po.get("po_no") or "物品"))
    qty = po.get("total_qty")
    qty_s = str(qty).rstrip("0").rstrip(".") if qty is not None else "0"
    name = f"{day}-{applicant}-{summary}-{qty_s}{ext}"
    return re.sub(r"[\\/:*?\"<>|]", "_", name)[:180]


def save_named_receipt(*, po: dict, original_name: str, raw: bytes, content_type: str, user: CurrentUser) -> dict:
    from app.services.warehouse_gap import ATTACH_EXTS, ATTACH_MAX_BYTES, ATTACH_MAX_FILES, count_po_attachments

    ensure_ux_schema()
    maybe_archive_old_receipts()
    ext = Path(original_name).suffix.lower()
    if ext not in ATTACH_EXTS:
        raise HTTPException(status_code=400, detail="仅支持 jpg / png / pdf")
    if len(raw) > ATTACH_MAX_BYTES:
        raise HTTPException(status_code=400, detail="单个附件不能超过 10MB")
    if count_po_attachments(int(po["id"])) >= ATTACH_MAX_FILES:
        raise HTTPException(status_code=400, detail=f"最多上传 {ATTACH_MAX_FILES} 个附件")

    biz = po.get("biz_date")
    if hasattr(biz, "strftime"):
        ym = biz.strftime("%Y%m")
    else:
        ym = str(biz or date.today())[:7].replace("-", "")
    folder = receipts_root() / ym
    folder.mkdir(parents=True, exist_ok=True)
    safe = receipt_display_name(po, ext)
    dest = folder / safe
    if dest.exists():
        i = 1
        stem = Path(safe).stem
        while True:
            cand = folder / f"{stem}_{i}{ext}"
            if not cand.exists():
                dest = cand
                safe = cand.name
                break
            i += 1
    dest.write_bytes(raw)
    rel = f"receipts/{ym}/{safe}"
    c_uid, c_name, u_uid, u_name = audit_tuple(user)
    aid = execute(
        """
        INSERT INTO wh_attachments
          (ref_type, ref_id, file_name, storage_relative_path, file_size, content_type,
           created_by_user_id, created_by_name, updated_by_user_id, updated_by_name)
        VALUES ('purchase_order',%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (po["id"], safe, rel, len(raw), content_type or "", c_uid, c_name, u_uid, u_name),
    )
    write_op_log(
        domain="aux",
        action="upload",
        entity_type="purchase_order",
        entity_id=int(po["id"]),
        entity_no=str(po.get("po_no") or ""),
        user=user,
        change_summary={"op": "upload", "file_name": safe, "size": len(raw), "path": rel},
    )
    return fetch_one("SELECT * FROM wh_attachments WHERE id=%s", (aid,)) or {}


def search_receipts(keyword: str) -> list[dict]:
    ensure_ux_schema()
    maybe_archive_old_receipts()
    kw = f"%{(keyword or '').strip()}%"
    sql = """
        SELECT a.*, p.po_no, p.purchaser_name, p.summary, p.biz_date, p.total_qty
        FROM wh_attachments a
        LEFT JOIN wh_purchase_orders p ON p.id = a.ref_id AND a.ref_type='purchase_order'
        WHERE 1=1
    """
    params: list = []
    if (keyword or "").strip():
        sql += """ AND (a.file_name LIKE %s OR a.storage_relative_path LIKE %s
                    OR p.purchaser_name LIKE %s OR p.summary LIKE %s OR p.po_no LIKE %s)"""
        params.extend([kw, kw, kw, kw, kw])
    sql += " ORDER BY a.id DESC LIMIT 200"
    rows = fetch_all(sql, tuple(params))
    out = []
    packed: set[str] = set()
    for r in rows:
        ym = r.get("archived_ym") or ""
        rel = str(r.get("storage_relative_path") or "")
        if not ym and rel.startswith("receipts/"):
            parts = rel.split("/")
            if len(parts) >= 2:
                folder_ym = parts[1]
                zip_path = receipts_archives_root() / f"{folder_ym}.zip"
                if zip_path.exists():
                    ym = folder_ym
        if ym:
            if ym in packed:
                continue
            packed.add(ym)
            out.append(
                {
                    "kind": "zip",
                    "archived_ym": ym,
                    "file_name": f"{ym}.zip",
                    "title": f"{ym} 月收据已归档，请下载压缩包",
                }
            )
        else:
            item = dict(r)
            item["kind"] = "file"
            out.append(item)
    return out
