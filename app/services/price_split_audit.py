"""价格拆分表操作日志：操作人、时间、变更内容。"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.api.deps import CurrentUser
from app.core.database import execute
from app.services.price_split_schema import ALL_DATA_COLUMNS, NUMERIC_FIELDS


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value


def snapshot_keys(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "material_group": item.get("material_group") or "",
        "material_no": item.get("material_no") or "",
        "drawing_no": item.get("drawing_no") or "",
        "net_price": _jsonable(item.get("net_price")),
        "price_version": item.get("price_version") or "",
    }


def diff_fields(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key in ALL_DATA_COLUMNS:
        left = _jsonable(before.get(key))
        right = _jsonable(after.get(key))
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
    region: str,
    action: str,
    user: CurrentUser,
    row_id: int | None = None,
    material_group: str | None = None,
    material_no: str | None = None,
    change_summary: dict | str | None = None,
) -> int:
    summary = change_summary
    if isinstance(summary, dict):
        summary = json.dumps(summary, ensure_ascii=False, default=str)
    return execute(
        """
        INSERT INTO price_split_operation_logs
            (region, row_id, material_group, material_no,
             action, operator_user_id, operator_name, change_summary)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            region,
            row_id,
            material_group or "",
            material_no or "",
            action,
            user.id,
            user.name,
            summary,
        ),
    )
