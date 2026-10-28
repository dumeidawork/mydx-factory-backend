"""实验建档变动后同步打字要求，并通知该明细的上传者与更新者。"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException, status

from app.api.deps import CurrentUser
from app.core.database import execute, fetch_all, fetch_one

logger = logging.getLogger(__name__)

STATUS_PENDING = "待确认"
STATUS_CONFIRMED = "已确认"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS workbench_typing_notices (
    id INT PRIMARY KEY AUTO_INCREMENT,
    order_no VARCHAR(64) NOT NULL COMMENT '订单号',
    contract_archive_id INT NULL COMMENT '关联合同 id',
    contract_file_name VARCHAR(255) NOT NULL DEFAULT '' COMMENT '合同文件名快照',
    owner_user_id INT NOT NULL COMMENT '通知接收人',
    owner_name VARCHAR(64) NULL,
    trigger_user_id INT NULL COMMENT '触发建档的操作人',
    trigger_user_name VARCHAR(64) NULL,
    changed_rows TEXT NULL COMMENT '未对齐明细 JSON',
    status VARCHAR(16) NOT NULL DEFAULT '待确认' COMMENT '待确认/已确认',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    confirmed_at DATETIME NULL,
    confirmed_by_user_id INT NULL,
    INDEX idx_typing_notice_owner_status (owner_user_id, status),
    INDEX idx_typing_notice_order (order_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实验建档变动打字要求工作台通知'
"""

SELECT_SQL = """
    id, order_no, contract_archive_id, contract_file_name,
    owner_user_id, owner_name, trigger_user_id, trigger_user_name, changed_rows,
    status, created_at, confirmed_at, confirmed_by_user_id
"""

_TABLES_READY = False


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _qty_key(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        try:
            return str(int(float(value)))
        except (TypeError, ValueError):
            return str(value).strip()


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _table_exists(table: str) -> bool:
    row = fetch_one(
        """
        SELECT 1 AS n
        FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = %s
        LIMIT 1
        """,
        (table,),
    )
    return bool(row)


def _has_column(column_name: str) -> bool:
    found = fetch_one(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'workbench_typing_notices'
          AND COLUMN_NAME = %s
        """,
        (column_name,),
    )
    return bool(found and int(found.get("cnt") or 0) > 0)


def _ensure_notice_columns() -> None:
    if not _has_column("changed_rows"):
        execute(
            """
            ALTER TABLE workbench_typing_notices
            ADD COLUMN changed_rows TEXT NULL COMMENT '未对齐明细 JSON' AFTER owner_name
            """
        )
    if not _has_column("trigger_user_id"):
        execute(
            """
            ALTER TABLE workbench_typing_notices
            ADD COLUMN trigger_user_id INT NULL COMMENT '触发建档的操作人' AFTER owner_name
            """
        )
    if not _has_column("trigger_user_name"):
        execute(
            """
            ALTER TABLE workbench_typing_notices
            ADD COLUMN trigger_user_name VARCHAR(64) NULL AFTER trigger_user_id
            """
        )


def ensure_typing_notice_table() -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    execute(CREATE_TABLE_SQL)
    _ensure_notice_columns()
    _TABLES_READY = True


def _empty_sync_result(**overrides: Any) -> dict[str, Any]:
    result = {
        "inserted_count": 0,
        "updated_count": 0,
        "detail_count": 0,
        "order_detail_ids": [],
        "notices": [],
        "error": None,
    }
    result.update(overrides)
    return result


def _recipient(user_id: int, name: str) -> dict[str, Any]:
    return {
        "contract_archive_id": None,
        "contract_file_name": "",
        "owner_user_id": user_id,
        "owner_name": _norm(name),
    }


def _recipients_for_details(details: list[dict[str, Any]], user: CurrentUser) -> list[dict[str, Any]]:
    by_id: dict[int, dict[str, Any]] = {}
    for row in details:
        pairs = (
            (_as_int(row.get("created_by_user_id")), _norm(row.get("created_by"))),
            (_as_int(row.get("updated_by_user_id")), _norm(row.get("updated_by"))),
        )
        for user_id, name in pairs:
            if not user_id or user_id <= 0 or user_id in by_id:
                continue
            by_id[user_id] = _recipient(user_id, name)
    if not by_id:
        by_id[int(user.id)] = _recipient(int(user.id), user.name)
    return list(by_id.values())


def _parse_changed_rows(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    text = _norm(raw)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [row for row in parsed if isinstance(row, dict)]


def _snapshot_from_detail(detail: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_detail_id": int(detail["id"]),
        "order_no": _norm(detail.get("order_no")),
        "item_no": _as_int(detail.get("item_no")),
        "material_no": _norm(detail.get("material_no")),
        "drawing_no": _norm(detail.get("drawing_no")),
        "quantity": _as_int(detail.get("quantity")),
        "heat_no": _norm(detail.get("heat_no")),
        "heat_treatment_batch_no": _norm(detail.get("heat_treatment_batch_no")),
    }


def _watch_changed(before: dict[str, Any] | None, after: dict[str, Any]) -> bool:
    old = before or {}
    return (
        _qty_key(old.get("quantity")) != _qty_key(after.get("quantity"))
        or _norm(old.get("heat_no")) != _norm(after.get("heat_no"))
        or _norm(old.get("heat_treatment_batch_no")) != _norm(after.get("heat_treatment_batch_no"))
    )


def _is_aligned(detail: dict[str, Any], typing: dict[str, Any] | None) -> bool:
    if not typing:
        return False
    return (
        _qty_key(detail.get("quantity")) == _qty_key(typing.get("quantity"))
        and _norm(detail.get("heat_no")) == _norm(typing.get("heat_no"))
        and _norm(detail.get("heat_treatment_batch_no")) == _norm(typing.get("heat_treatment_batch_no"))
    )


def _load_details_by_ids(ids: list[int]) -> dict[int, dict[str, Any]]:
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    rows = fetch_all(
        f"""
        SELECT id, order_no, item_no, drawing_no, material_no, quantity, heat_no, heat_treatment_batch_no,
               created_by, created_by_user_id, updated_by, updated_by_user_id
        FROM order_details
        WHERE id IN ({placeholders})
        """,
        tuple(ids),
    )
    return {int(row["id"]): row for row in rows}


def _load_typing_by_detail_ids(ids: list[int]) -> dict[int, dict[str, Any]]:
    if not ids or not _table_exists("typing_requirements"):
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    rows = fetch_all(
        f"""
        SELECT order_detail_id, quantity, heat_no, heat_treatment_batch_no
        FROM typing_requirements
        WHERE order_detail_id IN ({placeholders})
        """,
        tuple(ids),
    )
    return {int(row["order_detail_id"]): row for row in rows if row.get("order_detail_id")}


def _filter_unaligned_details(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = [int(row["id"]) for row in details]
    typing_map = _load_typing_by_detail_ids(ids)
    return [row for row in details if not _is_aligned(row, typing_map.get(int(row["id"])))]


def _filter_still_unaligned(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = sorted({int(row["order_detail_id"]) for row in snapshots if row.get("order_detail_id")})
    details = _load_details_by_ids(ids)
    typing_map = _load_typing_by_detail_ids(ids)
    result: list[dict[str, Any]] = []
    for snap in snapshots:
        detail_id = int(snap.get("order_detail_id") or 0)
        detail = details.get(detail_id)
        if not detail:
            continue
        if not _is_aligned(detail, typing_map.get(detail_id)):
            result.append(_snapshot_from_detail(detail))
    return result


def _find_pending(order_no: str, owner_user_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT {SELECT_SQL}
        FROM workbench_typing_notices
        WHERE order_no=%s AND owner_user_id=%s AND status=%s
        LIMIT 1
        """,
        (order_no, owner_user_id, STATUS_PENDING),
    )


def _merge_changed_rows(existing_raw: Any, incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[int, dict[str, Any]] = {}
    for row in _parse_changed_rows(existing_raw) + incoming:
        detail_id = int(row.get("order_detail_id") or 0)
        if detail_id <= 0:
            continue
        by_id[detail_id] = row
    return list(by_id.values())


def _insert_notice(
    order_no: str,
    recipient: dict[str, Any],
    changed_rows: list[dict[str, Any]],
    user: CurrentUser,
) -> int:
    return execute(
        """
        INSERT INTO workbench_typing_notices
            (order_no, contract_archive_id, contract_file_name, owner_user_id, owner_name,
             trigger_user_id, trigger_user_name, changed_rows, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            order_no,
            recipient.get("contract_archive_id"),
            recipient.get("contract_file_name") or "",
            recipient["owner_user_id"],
            recipient.get("owner_name") or "",
            user.id,
            _norm(user.name),
            json.dumps(changed_rows, ensure_ascii=False, default=str),
            STATUS_PENDING,
        ),
    )


def _mark_confirmed(notice_id: int, user_id: int | None) -> None:
    execute(
        """
        UPDATE workbench_typing_notices
        SET status=%s, confirmed_at=NOW(), confirmed_by_user_id=%s
        WHERE id=%s
        """,
        (STATUS_CONFIRMED, user_id, notice_id),
    )


def _notify_detail_owners(
    unaligned: list[dict[str, Any]],
    user: CurrentUser,
) -> list[dict[str, Any]]:
    ensure_typing_notice_table()
    ids = [int(row["id"]) for row in unaligned if row.get("id")]
    fresh = _load_details_by_ids(ids)
    details = [{**row, **(fresh.get(int(row["id"])) or {}), "id": int(row["id"])} for row in unaligned]

    by_order: dict[str, list[dict[str, Any]]] = {}
    for row in details:
        order_no = _norm(row.get("order_no"))
        if not order_no:
            continue
        by_order.setdefault(order_no, []).append(_snapshot_from_detail(row))

    results: list[dict[str, Any]] = []
    for order_no, snapshots in by_order.items():
        order_details = [row for row in details if _norm(row.get("order_no")) == order_no]
        recipients = _recipients_for_details(order_details, user)
        for recipient in recipients:
            pending = _find_pending(order_no, int(recipient["owner_user_id"]))
            if pending:
                merged = _merge_changed_rows(pending.get("changed_rows"), snapshots)
                execute(
                    """
                    UPDATE workbench_typing_notices
                    SET changed_rows=%s, trigger_user_id=%s, trigger_user_name=%s,
                        contract_archive_id=COALESCE(%s, contract_archive_id),
                        contract_file_name=CASE
                            WHEN %s='' THEN contract_file_name ELSE %s
                        END
                    WHERE id=%s
                    """,
                    (
                        json.dumps(merged, ensure_ascii=False, default=str),
                        user.id,
                        _norm(user.name),
                        recipient.get("contract_archive_id"),
                        recipient.get("contract_file_name") or "",
                        recipient.get("contract_file_name") or "",
                        pending["id"],
                    ),
                )
                results.append(
                    {
                        "order_no": order_no,
                        "created": False,
                        "updated": True,
                        "owner_name": recipient.get("owner_name") or "",
                        "reason": "merged",
                        "unaligned_count": len(merged),
                    }
                )
                continue
            _insert_notice(order_no, recipient, snapshots, user)
            results.append(
                {
                    "order_no": order_no,
                    "created": True,
                    "updated": False,
                    "owner_name": recipient.get("owner_name") or "",
                    "reason": "created",
                    "unaligned_count": len(snapshots),
                }
            )
    return results


def _generate_for_details(details: list[dict[str, Any]], user: CurrentUser) -> dict[str, Any]:
    from app.api.v1.typing_requirements import GenerateRequest, generate_typing_requirements

    by_order: dict[str, list[int]] = {}
    for row in details:
        order_no = _norm(row.get("order_no"))
        detail_id = int(row.get("id") or 0)
        if not order_no or detail_id <= 0:
            continue
        by_order.setdefault(order_no, []).append(detail_id)

    inserted = 0
    updated = 0
    for order_no, ids in by_order.items():
        unique_ids = sorted(set(ids))
        generated = generate_typing_requirements(
            GenerateRequest(order_no=order_no, order_detail_ids=unique_ids, force_overwrite=False),
            user,
        )
        inserted += int(generated.get("inserted_count") or 0)
        updated += int(generated.get("updated_count") or 0)
    return {"inserted_count": inserted, "updated_count": updated}


def _sync_typing_after_experiment_change(
    updated_rows: list[dict[str, Any]],
    before_map: dict[int, dict[str, Any]],
    user: CurrentUser,
) -> dict[str, Any]:
    ensure_typing_notice_table()
    changed = [row for row in updated_rows if row.get("id") and _watch_changed(before_map.get(int(row["id"])), row)]
    if not changed:
        return _empty_sync_result()
    unaligned = _filter_unaligned_details(changed)
    if not unaligned:
        return _empty_sync_result()

    generated = _generate_for_details(unaligned, user)
    notices = _notify_detail_owners(unaligned, user)
    order_detail_ids = sorted({int(row["id"]) for row in unaligned})
    return _empty_sync_result(
        inserted_count=int(generated.get("inserted_count") or 0),
        updated_count=int(generated.get("updated_count") or 0),
        detail_count=len(unaligned),
        order_detail_ids=order_detail_ids,
        notices=notices,
    )


def sync_typing_after_experiment_change(
    updated_rows: list[dict[str, Any]],
    before_map: dict[int, dict[str, Any]],
    user: CurrentUser,
) -> dict[str, Any]:
    """保存成功后的副作用：三字段变化且未对齐则生成打字要求并通知上传者/更新者。失败不抛。"""
    try:
        return _sync_typing_after_experiment_change(updated_rows, before_map, user)
    except Exception as exc:
        logger.exception("sync typing after experiment change failed")
        detail = getattr(exc, "detail", None)
        if not isinstance(detail, str) or not detail.strip():
            detail = str(exc) or "打字要求同步失败"
        return _empty_sync_result(error=detail)


def maybe_notify_experiment_typing_change(
    updated_rows: list[dict[str, Any]],
    before_map: dict[int, dict[str, Any]],
    user: CurrentUser,
) -> dict[str, Any]:
    return sync_typing_after_experiment_change(updated_rows, before_map, user)


def sync_pending_notices_for_order(order_no: str, *, confirm_notice_id: int | None = None, user_id: int | None = None) -> None:
    ensure_typing_notice_table()
    order_no = _norm(order_no)
    if not order_no:
        return
    notices = fetch_all(
        f"""
        SELECT {SELECT_SQL}
        FROM workbench_typing_notices
        WHERE order_no=%s AND status=%s
        """,
        (order_no, STATUS_PENDING),
    )
    for notice in notices:
        notice_id = int(notice["id"])
        still = _filter_still_unaligned(_parse_changed_rows(notice.get("changed_rows")))
        if notice_id == confirm_notice_id or not still:
            _mark_confirmed(notice_id, user_id)
            continue
        execute(
            "UPDATE workbench_typing_notices SET changed_rows=%s WHERE id=%s",
            (json.dumps(still, ensure_ascii=False, default=str), notice_id),
        )


def _row_to_item(row: dict) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "order_no": _norm(row.get("order_no")),
        "contract_archive_id": int(row["contract_archive_id"]) if row.get("contract_archive_id") else None,
        "contract_file_name": _norm(row.get("contract_file_name")),
        "owner_user_id": int(row["owner_user_id"]) if row.get("owner_user_id") else None,
        "owner_name": _norm(row.get("owner_name")),
        "trigger_user_id": int(row["trigger_user_id"]) if row.get("trigger_user_id") else None,
        "trigger_user_name": _norm(row.get("trigger_user_name")),
        "changed_rows": _parse_changed_rows(row.get("changed_rows")),
        "status": _norm(row.get("status")) or STATUS_PENDING,
        "created_at": str(row.get("created_at") or ""),
        "confirmed_at": str(row.get("confirmed_at") or "") or None,
        "confirmed_by_user_id": int(row["confirmed_by_user_id"]) if row.get("confirmed_by_user_id") else None,
    }


def get_pending_typing_notice_items(user_id: int) -> list[dict[str, Any]]:
    ensure_typing_notice_table()
    rows = fetch_all(
        f"""
        SELECT {SELECT_SQL}
        FROM workbench_typing_notices
        WHERE owner_user_id=%s AND status=%s
        ORDER BY created_at DESC, id DESC
        """,
        (user_id, STATUS_PENDING),
    )
    return [_row_to_item(row) for row in rows]


def get_typing_notice(notice_id: int) -> dict[str, Any] | None:
    ensure_typing_notice_table()
    row = fetch_one(
        f"SELECT {SELECT_SQL} FROM workbench_typing_notices WHERE id=%s",
        (notice_id,),
    )
    return _row_to_item(row) if row else None


def confirm_typing_notice(notice_id: int, user: CurrentUser) -> dict[str, Any]:
    ensure_typing_notice_table()
    row = fetch_one(
        f"SELECT {SELECT_SQL} FROM workbench_typing_notices WHERE id=%s",
        (notice_id,),
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="通知不存在")
    owner_id = int(row.get("owner_user_id") or 0)
    if owner_id != int(user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅通知接收人可以处理该提醒")
    if _norm(row.get("status")) != STATUS_PENDING:
        raise HTTPException(status_code=400, detail="该通知已确认")

    snapshots = _parse_changed_rows(row.get("changed_rows"))
    order_no = _norm(row.get("order_no"))
    order_detail_ids = sorted(
        {int(item.get("order_detail_id") or 0) for item in snapshots if int(item.get("order_detail_id") or 0) > 0}
    )
    generated = None
    still = _filter_still_unaligned(snapshots)
    if still:
        details = []
        loaded = _load_details_by_ids([int(item.get("order_detail_id") or 0) for item in still])
        for item in still:
            detail = loaded.get(int(item.get("order_detail_id") or 0))
            if detail:
                details.append(detail)
        if details:
            try:
                generated = _generate_for_details(details, user)
            except Exception:
                logger.exception("backfill typing generate on notice confirm failed")
                generated = {"error": "补生成打字要求失败"}

    sync_pending_notices_for_order(order_no, confirm_notice_id=notice_id, user_id=user.id)
    updated = fetch_one(
        f"SELECT {SELECT_SQL} FROM workbench_typing_notices WHERE id=%s",
        (notice_id,),
    )
    return {
        "item": _row_to_item(updated or row),
        "order_no": order_no,
        "order_detail_ids": order_detail_ids,
        "generated": generated,
    }


def generate_from_typing_notice(notice_id: int, order_detail_ids: list[int], user: CurrentUser) -> dict[str, Any]:
    notice = get_typing_notice(notice_id)
    if not notice:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="通知不存在")
    if int(notice.get("owner_user_id") or 0) != int(user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅通知接收人可以处理该提醒")
    if _norm(notice.get("status")) != STATUS_PENDING:
        raise HTTPException(status_code=400, detail="该通知已确认")
    allowed = {int(row.get("order_detail_id") or 0) for row in notice.get("changed_rows") or []}
    selected = sorted({int(item) for item in order_detail_ids if int(item) > 0 and int(item) in allowed})
    if not selected:
        raise HTTPException(status_code=400, detail="请勾选要自动生成的明细")
    from app.api.v1.typing_requirements import GenerateRequest, generate_typing_requirements

    generated = generate_typing_requirements(
        GenerateRequest(order_no=notice["order_no"], order_detail_ids=selected, force_overwrite=False),
        user,
    )
    sync_pending_notices_for_order(notice["order_no"], confirm_notice_id=notice_id, user_id=user.id)
    return {
        "status": "success",
        "notice_id": notice_id,
        "generated": generated,
    }
