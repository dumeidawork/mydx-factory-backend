"""辅料仓库缺口补齐：建表、现金角色闸、附件、价格预警。"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from fastapi import HTTPException

from app.api.deps import CurrentUser
from app.core.database import execute, fetch_all, fetch_one
from app.core.paths import get_warehouse_dir
from app.services.warehouse_audit import audit_tuple, update_audit_tuple, write_op_log

PRICE_ALERT_RATIO = Decimal("0.10")
ATTACH_MAX_FILES = 5
ATTACH_MAX_BYTES = 10 * 1024 * 1024
ATTACH_EXTS = {".jpg", ".jpeg", ".png", ".pdf"}

# 推进时按「当前状态」校验角色；super_admin / general_manager 可覆盖核实与付款
CASH_FROM_ROLES: dict[str, set[str] | None] = {
    "draft": None,  # 任意登录用户可提交核实
    "pending_verify": {"warehouse", "super_admin", "general_manager"},
    "pending_approve": {"general_manager", "super_admin"},
    "pending_pay": {"warehouse", "finance", "super_admin", "general_manager"},
    "paid": {"warehouse", "finance", "super_admin", "general_manager"},
}

CASH_REJECT_TO = {
    "pending_verify": "draft",
    "pending_approve": "pending_verify",
    "pending_pay": "pending_approve",
}

GAP_TABLES: list[tuple[str, str]] = [
    (
        "wh_attachments",
        """
        CREATE TABLE IF NOT EXISTS wh_attachments (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          ref_type VARCHAR(32) NOT NULL,
          ref_id BIGINT NOT NULL,
          file_name VARCHAR(255) NOT NULL,
          storage_relative_path VARCHAR(1024) NOT NULL,
          file_size BIGINT NOT NULL DEFAULT 0,
          content_type VARCHAR(128) NULL,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          KEY idx_wh_att_ref (ref_type, ref_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='仓库附件'
        """,
    ),
    (
        "wh_cash_purchase_approvals",
        """
        CREATE TABLE IF NOT EXISTS wh_cash_purchase_approvals (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          po_id BIGINT NOT NULL,
          from_status VARCHAR(32) NOT NULL,
          to_status VARCHAR(32) NOT NULL,
          actor_user_id BIGINT NULL,
          actor_name VARCHAR(64) NULL,
          comment VARCHAR(255) NULL,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          KEY idx_wh_cpa_po (po_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='现金采购审批轨迹'
        """,
    ),
    (
        "wh_price_history",
        """
        CREATE TABLE IF NOT EXISTS wh_price_history (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          item_id BIGINT NOT NULL,
          supplier_id BIGINT NULL,
          unit_price DECIMAL(12,4) NOT NULL,
          biz_date DATE NOT NULL,
          po_id BIGINT NULL,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          KEY idx_wh_ph_item (item_id, biz_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购价格历史'
        """,
    ),
    (
        "wh_supplier_month_invoices",
        """
        CREATE TABLE IF NOT EXISTS wh_supplier_month_invoices (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          supplier_name VARCHAR(100) NOT NULL,
          month_key VARCHAR(7) NOT NULL,
          status VARCHAR(16) NOT NULL DEFAULT 'unissued',
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_wh_smi (supplier_name, month_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='供应商月度发票标记'
        """,
    ),
]

_ready = False


def ensure_gap_tables() -> None:
    global _ready
    if _ready:
        return
    for name, ddl in GAP_TABLES:
        row = fetch_one(
            """
            SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
            """,
            (name,),
        )
        if not row:
            execute(ddl)
    _ready = True


def require_cash_role(user: CurrentUser, from_status: str) -> None:
    allowed = CASH_FROM_ROLES.get(from_status)
    if allowed is None:
        return
    if (user.role_code or "") not in allowed:
        raise HTTPException(status_code=403, detail=f"当前角色无权操作此审批节点（需要：{'/'.join(sorted(allowed))}）")


def record_cash_approval(
    *,
    po_id: int,
    from_status: str,
    to_status: str,
    user: CurrentUser,
    comment: str | None,
) -> None:
    ensure_gap_tables()
    c_uid, c_name, u_uid, u_name = audit_tuple(user)
    execute(
        """
        INSERT INTO wh_cash_purchase_approvals
          (po_id, from_status, to_status, actor_user_id, actor_name, comment,
           created_by_user_id, created_by_name, updated_by_user_id, updated_by_name)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (po_id, from_status, to_status, user.id, user.name, comment, c_uid, c_name, u_uid, u_name),
    )


def count_po_attachments(po_id: int) -> int:
    ensure_gap_tables()
    row = fetch_one(
        "SELECT COUNT(*) AS c FROM wh_attachments WHERE ref_type='purchase_order' AND ref_id=%s",
        (po_id,),
    )
    return int(row["c"] if row else 0)


def list_po_attachments(po_id: int) -> list[dict]:
    ensure_gap_tables()
    return fetch_all(
        "SELECT * FROM wh_attachments WHERE ref_type='purchase_order' AND ref_id=%s ORDER BY id",
        (po_id,),
    )


def _safe_filename(name: str) -> str:
    stem = Path(name or "file").name
    keep = []
    for ch in stem:
        keep.append(ch if ch.isalnum() or ch in "-_. " else "_")
    out = "".join(keep).strip() or "file"
    return out[:180]


def save_purchase_attachment(
    *,
    po: dict,
    original_name: str,
    raw: bytes,
    content_type: str,
    user: CurrentUser,
) -> dict:
    from app.services.warehouse_ux import save_named_receipt

    return save_named_receipt(
        po=po,
        original_name=original_name,
        raw=raw,
        content_type=content_type,
        user=user,
    )


def attachment_abs_path(row: dict) -> Path:
    root = get_warehouse_dir().resolve()
    rel = str(row.get("storage_relative_path") or "")
    target = (root / rel).resolve()
    if not str(target).startswith(str(root)):
        raise HTTPException(status_code=400, detail="非法文件路径")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="附件文件不存在")
    return target


def record_price_and_alerts(
    *,
    item_id: int | None,
    supplier_id: int | None,
    unit_price: Decimal,
    biz_date,
    po_id: int,
    name_spec: str,
    user: CurrentUser,
) -> dict | None:
    """写入价格历史；高于历史最高价 10% 时写预警。无 item_id 或无历史则不预警。"""
    if not item_id:
        return None
    ensure_gap_tables()
    hist = fetch_one(
        "SELECT MAX(unit_price) AS mx FROM wh_price_history WHERE item_id=%s",
        (item_id,),
    )
    c_uid, c_name, u_uid, u_name = audit_tuple(user)
    execute(
        """
        INSERT INTO wh_price_history
          (item_id, supplier_id, unit_price, biz_date, po_id,
           created_by_user_id, created_by_name, updated_by_user_id, updated_by_name)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (item_id, supplier_id, unit_price, biz_date, po_id, c_uid, c_name, u_uid, u_name),
    )
    mx = hist.get("mx") if hist else None
    if mx is None:
        return None
    max_price = Decimal(str(mx))
    if max_price <= 0:
        return None
    if unit_price > max_price * (1 + PRICE_ALERT_RATIO):
        title = (
            f"{name_spec} 采购价异常：本次 {float(unit_price)} 元，"
            f"高于历史最高 {float(max_price)} 元超过 10%"
        )
        execute(
            """
            INSERT INTO wh_alerts
              (domain, alert_type, title, level, ref_type, ref_id, is_resolved,
               created_by_user_id, created_by_name, updated_by_user_id, updated_by_name)
            VALUES ('aux','price_abnormal',%s,'warning','purchase_order',%s,0,%s,%s,%s,%s)
            """,
            (title, po_id, c_uid, c_name, u_uid, u_name),
        )
        return {
            "item_id": item_id,
            "name_spec": name_spec,
            "unit_price": float(unit_price),
            "hist_max": float(max_price),
            "title": title,
        }
    return None


def upsert_invoice(
    supplier: str,
    month_key: str,
    status: str,
    user: CurrentUser,
    diff_note: str | None = None,
) -> dict:
    from app.services.warehouse_ux import ensure_ux_schema

    ensure_ux_schema()
    if status not in ("issued", "unissued"):
        raise HTTPException(status_code=400, detail="发票状态无效")
    name = (supplier or "").strip() or "未指定"
    row = fetch_one(
        "SELECT * FROM wh_supplier_month_invoices WHERE supplier_name=%s AND month_key=%s",
        (name, month_key),
    )
    u_uid, u_name = update_audit_tuple(user)
    if row:
        if diff_note is not None:
            execute(
                """
                UPDATE wh_supplier_month_invoices
                SET status=%s, diff_note=%s, updated_by_user_id=%s, updated_by_name=%s
                WHERE id=%s
                """,
                (status, diff_note, u_uid, u_name, row["id"]),
            )
        else:
            execute(
                """
                UPDATE wh_supplier_month_invoices
                SET status=%s, updated_by_user_id=%s, updated_by_name=%s
                WHERE id=%s
                """,
                (status, u_uid, u_name, row["id"]),
            )
        iid = row["id"]
    else:
        c_uid, c_name, uu, un = audit_tuple(user)
        iid = execute(
            """
            INSERT INTO wh_supplier_month_invoices
              (supplier_name, month_key, status, diff_note,
               created_by_user_id, created_by_name, updated_by_user_id, updated_by_name)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (name, month_key, status, diff_note, c_uid, c_name, uu, un),
        )
    write_op_log(
        domain="aux",
        action="update",
        entity_type="supplier_invoice",
        entity_id=iid,
        entity_no=f"{name}/{month_key}",
        user=user,
        change_summary={"op": "invoice", "status": status, "diff_note": diff_note},
    )
    return fetch_one("SELECT * FROM wh_supplier_month_invoices WHERE id=%s", (iid,)) or {}


def invoice_map(month_key: str) -> dict[str, str]:
    rows = invoice_rows(month_key)
    return {k: str(v.get("status") or "unissued") for k, v in rows.items()}


def invoice_rows(month_key: str) -> dict[str, dict]:
    ensure_gap_tables()
    rows = fetch_all(
        "SELECT * FROM wh_supplier_month_invoices WHERE month_key=%s",
        (month_key,),
    )
    return {str(r["supplier_name"]): r for r in rows}


def refresh_named_alerts(
    *,
    alert_type: str,
    title_prefix: str,
    titles: list[str],
    level: str,
    user: CurrentUser,
) -> None:
    """先关闭同前缀未解决预警，再写入当前 titles。"""
    u_uid, u_name = update_audit_tuple(user)
    execute(
        """
        UPDATE wh_alerts SET is_resolved=1, updated_by_user_id=%s, updated_by_name=%s
        WHERE domain='aux' AND alert_type=%s AND is_resolved=0 AND title LIKE %s
        """,
        (u_uid, u_name, alert_type, f"{title_prefix}%"),
    )
    if not titles:
        return
    c_uid, c_name, uu, un = audit_tuple(user)
    for title in titles:
        execute(
            """
            INSERT INTO wh_alerts
              (domain, alert_type, title, level, ref_type, is_resolved,
               created_by_user_id, created_by_name, updated_by_user_id, updated_by_name)
            VALUES ('aux', %s, %s, %s, %s, 0, %s, %s, %s, %s)
            """,
            (alert_type, title, level, alert_type, c_uid, c_name, uu, un),
        )
