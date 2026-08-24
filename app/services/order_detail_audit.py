"""订单明细审计：行内上传者/更改者 + 操作日志。"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.api.deps import CurrentUser
from app.core.database import execute

CREATE_LOG_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS order_detail_operation_logs (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            row_id BIGINT NULL COMMENT '订单明细ID，删除后可空',
            order_no VARCHAR(64) NULL,
            material_no VARCHAR(128) NULL,
            action VARCHAR(32) NOT NULL COMMENT 'create/update/upload/export/delete',
            operator_user_id INT NULL,
            operator_name VARCHAR(64) NOT NULL DEFAULT '',
            operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            change_summary TEXT NULL COMMENT '变更内容 JSON',
            INDEX idx_od_log_row (row_id),
            INDEX idx_od_log_order (order_no),
            INDEX idx_od_log_material (material_no),
            INDEX idx_od_log_action (action),
            INDEX idx_od_log_time (operated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单明细操作日志'
    """

AUDIT_COLUMN_ALTERS = (
    (
        "created_by",
        "ALTER TABLE order_details ADD COLUMN created_by VARCHAR(64) NULL COMMENT '上传者' AFTER updated_at",
    ),
    (
        "created_by_user_id",
        "ALTER TABLE order_details ADD COLUMN created_by_user_id INT NULL COMMENT '上传者用户ID' AFTER created_by",
    ),
    (
        "updated_by",
        "ALTER TABLE order_details ADD COLUMN updated_by VARCHAR(64) NULL COMMENT '更改者' AFTER created_by_user_id",
    ),
    (
        "updated_by_user_id",
        "ALTER TABLE order_details ADD COLUMN updated_by_user_id INT NULL COMMENT '更改者用户ID' AFTER updated_by",
    ),
)

AUDIT_CREATE_COLUMNS = "created_by, created_by_user_id, updated_by, updated_by_user_id"
AUDIT_UPDATE_SET = "updated_by=%s, updated_by_user_id=%s, updated_at=NOW()"

SNAPSHOT_KEYS = (
    "id",
    "order_no",
    "customer",
    "factory_order_no",
    "seq",
    "item_no",
    "name",
    "drawing_no",
    "material_no",
    "spec_model",
    "quantity",
    "unit_weight",
    "total_weight",
    "agreement_price",
    "product_unit_price",
    "status",
    "upload_type",
    "material_mode",
)

NUMERIC_FIELDS = frozenset(
    {
        "seq",
        "item_no",
        "quantity",
        "unit_weight",
        "total_weight",
        "agreement_price",
        "product_unit_price",
        "id",
    }
)

DIFF_KEYS = (
    "customer",
    "factory_order_no",
    "seq",
    "item_no",
    "name",
    "drawing_no",
    "material_no",
    "spec_model",
    "spec",
    "standard",
    "material",
    "quantity",
    "unit_weight",
    "total_weight",
    "agreement_price",
    "product_unit_price",
    "remark1",
    "remark2",
    "heat_no",
    "heat_treatment_batch_no",
    "status",
    "upload_type",
    "material_mode",
    "doc_status",
)


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value


def snapshot_keys(item: dict[str, Any]) -> dict[str, Any]:
    return {key: jsonable(item.get(key)) for key in SNAPSHOT_KEYS}


def create_audit_values(user: CurrentUser) -> tuple[str, int, str, int]:
    return user.name, user.id, user.name, user.id


def update_audit_values(user: CurrentUser) -> tuple[str, int]:
    return user.name, user.id


def stamp_create(record: dict[str, Any], user: CurrentUser) -> dict[str, Any]:
    record["created_by"] = user.name
    record["created_by_user_id"] = user.id
    record["updated_by"] = user.name
    record["updated_by_user_id"] = user.id
    return record


def stamp_update(record: dict[str, Any], user: CurrentUser, now: str | None = None) -> dict[str, Any]:
    record["updated_by"] = user.name
    record["updated_by_user_id"] = user.id
    if now is not None:
        record["updated_at"] = now
    return record


def diff_fields(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key in DIFF_KEYS:
        left = jsonable(before.get(key))
        right = jsonable(after.get(key))
        if key in NUMERIC_FIELDS:
            try:
                if abs(float(left or 0) - float(right or 0)) < 1e-6:
                    continue
            except (TypeError, ValueError):
                if left == right:
                    continue
        elif str(left or "").strip() == str(right or "").strip():
            continue
        out[key] = {"before": left, "after": right}
    return out


def write_op_log(
    *,
    action: str,
    user: CurrentUser,
    row_id: int | None = None,
    order_no: str | None = None,
    material_no: str | None = None,
    change_summary: dict | str | None = None,
) -> int:
    summary = change_summary
    if isinstance(summary, dict):
        summary = json.dumps(summary, ensure_ascii=False, default=str)
    return execute(
        """
        INSERT INTO order_detail_operation_logs
            (row_id, order_no, material_no, action, operator_user_id, operator_name, change_summary)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            row_id,
            order_no or "",
            material_no or "",
            action,
            user.id,
            user.name,
            summary,
        ),
    )
