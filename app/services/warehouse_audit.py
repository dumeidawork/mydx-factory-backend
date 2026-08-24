"""仓库模块审计：行内四字段 + 操作日志。"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.api.deps import CurrentUser
from app.core.database import execute


def serialize_value(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.strftime("%Y-%m-%d")
    return v


def serialize_row(row: dict | None) -> dict | None:
    if not row:
        return None
    return {k: serialize_value(v) for k, v in row.items()}


def serialize_rows(rows: list[dict]) -> list[dict]:
    return [serialize_row(r) or {} for r in rows]


def audit_tuple(user: CurrentUser) -> tuple[int, str, int, str]:
    """created_by_user_id, created_by_name, updated_by_user_id, updated_by_name"""
    return user.id, user.name, user.id, user.name


def update_audit_tuple(user: CurrentUser) -> tuple[int, str]:
    return user.id, user.name


def diff_fields(before: dict, after: dict, keys: list[str]) -> dict:
    out: dict[str, dict[str, Any]] = {}
    for key in keys:
        b = serialize_value(before.get(key))
        a = serialize_value(after.get(key))
        if b != a:
            out[key] = {"before": b, "after": a}
    return out


def write_op_log(
    *,
    domain: str,
    action: str,
    entity_type: str,
    user: CurrentUser,
    entity_id: int | None = None,
    entity_no: str | None = None,
    change_summary: dict | str | None = None,
    client_info: str | None = None,
) -> int:
    summary = change_summary
    if isinstance(summary, dict):
        summary = json.dumps(summary, ensure_ascii=False, default=str)
    return execute(
        """
        INSERT INTO wh_operation_logs
          (domain, action, entity_type, entity_id, entity_no,
           operator_user_id, operator_name, change_summary, client_info)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            domain,
            action,
            entity_type,
            entity_id,
            entity_no,
            user.id,
            user.name,
            summary,
            client_info,
        ),
    )


def gen_no(prefix: str) -> str:
    return f"{prefix}{datetime.now().strftime('%Y%m%d%H%M%S%f')[:17]}"
