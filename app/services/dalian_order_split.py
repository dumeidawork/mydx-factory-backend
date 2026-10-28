"""大连部分预定时拆分订单明细，并在数量回调时并回余量。"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.api.deps import CurrentUser
from app.core.database import execute, fetch_all, fetch_one, get_db
from app.services.order_detail_audit import create_audit_values, update_audit_values, write_op_log

_SPLIT_READY = False

_DETAIL_COLUMNS = """
    id, order_no, customer, factory_order_no, seq, item_no, name, drawing_no, material_no,
    spec_model, spec, standard, material, quantity, unit_weight, total_weight,
    agreement_price, product_unit_price, remark1, remark2, heat_no, heat_treatment_batch_no,
    order_status, doc_status, upload_type, material_mode, started_at, finished_at,
    split_group_id, contract_qty
"""


def ensure_split_columns() -> None:
    global _SPLIT_READY
    if _SPLIT_READY:
        return
    if not _column_exists("split_group_id"):
        execute(
            """
            ALTER TABLE order_details
            ADD COLUMN split_group_id INT NULL COMMENT '部分发货拆分组，首次拆分时为原明细id' AFTER shipped_at
            """
        )
    if not _column_exists("contract_qty"):
        execute(
            """
            ALTER TABLE order_details
            ADD COLUMN contract_qty INT NULL COMMENT '拆分前合同数量，同组各行相同' AFTER split_group_id
            """
        )
    if not _index_exists("idx_order_details_split_group"):
        execute("ALTER TABLE order_details ADD INDEX idx_order_details_split_group (split_group_id)")
    _SPLIT_READY = True


def reservation_is_locked(status: str, reservation_id: int | None) -> bool:
    if str(status or "").strip() in {"已装箱", "已发货"}:
        return True
    if not reservation_id:
        return False
    row = fetch_one(
        "SELECT id FROM dalian_shipping_details WHERE reservation_id=%s LIMIT 1",
        (int(reservation_id),),
    )
    return bool(row)


def assert_planned_qty_fits(order_detail_id: int, planned_qty: int, exclude_reservation_id: int | None) -> None:
    """预定数量不能超过本行加上可扣余量，且一行只能有一个到货日。"""
    ensure_split_columns()
    if planned_qty <= 0:
        return
    row = _load_row(order_detail_id)
    others = _other_planned_qty(order_detail_id, exclude_reservation_id)
    if others > 0:
        raise HTTPException(status_code=400, detail="该明细已有其他日期的预定，请对余量行另行预定")
    capacity = int(row["quantity"] or 0) + _pullable_qty(row)
    if planned_qty > capacity:
        raise HTTPException(
            status_code=400,
            detail=f"预定数量 {planned_qty} 超过可预定余量 {capacity}（本行数量 {int(row['quantity'] or 0)}）",
        )


def align_order_line_to_quantity(
    order_detail_id: int,
    target_qty: int,
    user: CurrentUser,
    *,
    remove_if_zero: bool = False,
) -> dict[str, Any]:
    """把订单明细数量调整到预定数量。差额从同组余量扣出或并回。"""
    ensure_split_columns()
    if target_qty < 0:
        raise HTTPException(status_code=400, detail="预定数量不能小于 0")
    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            result = _align_with_cursor(cursor, order_detail_id, target_qty, user, remove_if_zero=remove_if_zero)
        except Exception:
            conn.rollback()
            cursor.close()
            raise
        cursor.close()
    _after_change(result, user)
    return {
        "changed": result["changed"],
        "remainder_created": result["remainder_created"],
        "remainder_qty": result["remainder_qty"],
    }


def migrate_existing_partial_reservations(*, apply: bool) -> dict[str, Any]:
    """把已有的部分预定拆成独立订单明细。最早一笔留在原 id。默认只出清单。"""
    ensure_split_columns()
    details = fetch_all(
        """
        SELECT d.id
        FROM order_details d
        INNER JOIN dalian_shipping_reservations r ON r.order_detail_id = d.id AND r.planned_qty > 0
        WHERE d.order_no LIKE '4%'
        GROUP BY d.id
        ORDER BY d.id
        """
    )
    report: list[dict[str, Any]] = []
    applied = 0
    for item in details:
        detail_id = int(item["id"])
        entry = _plan_migration(detail_id)
        if not entry:
            continue
        report.append(entry)
        if apply and not entry.get("skip"):
            _apply_migration(detail_id, entry)
            applied += 1
    if apply and applied:
        _clear_order_cache()
    return {"apply": apply, "applied": applied, "count": len(report), "rows": report}


def _column_exists(column: str) -> bool:
    row = fetch_one(
        """
        SELECT 1 AS n FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name='order_details' AND column_name=%s
        LIMIT 1
        """,
        (column,),
    )
    return bool(row)


def _index_exists(index_name: str) -> bool:
    row = fetch_one(
        """
        SELECT 1 AS n FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name='order_details' AND index_name=%s
        LIMIT 1
        """,
        (index_name,),
    )
    return bool(row)


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _heat_key(row: dict[str, Any]) -> tuple[str, str]:
    return _norm(row.get("heat_no")), _norm(row.get("heat_treatment_batch_no"))


def _heats_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    heat_left = _heat_key(left)
    heat_right = _heat_key(right)
    if heat_left == ("", "") and heat_right == ("", ""):
        return True
    return heat_left == heat_right


def _heat_empty(row: dict[str, Any]) -> bool:
    return _heat_key(row) == ("", "")


def _as_int(value: Any) -> int:
    return int(value or 0)


def _total_weight(unit_weight: Any, qty: int) -> float:
    return round(float(unit_weight or 0) * int(qty), 2)


def _load_row(order_detail_id: int) -> dict[str, Any]:
    row = fetch_one(f"SELECT {_DETAIL_COLUMNS} FROM order_details WHERE id=%s", (int(order_detail_id),))
    if not row:
        raise HTTPException(status_code=404, detail="未找到订单明细")
    return row


def _other_planned_qty(order_detail_id: int, exclude_reservation_id: int | None) -> int:
    sql = """
        SELECT COALESCE(SUM(planned_qty), 0) AS qty
        FROM dalian_shipping_reservations
        WHERE order_detail_id=%s AND planned_qty > 0
    """
    params: list[Any] = [int(order_detail_id)]
    if exclude_reservation_id is not None:
        sql += " AND id<>%s"
        params.append(int(exclude_reservation_id))
    row = fetch_one(sql, tuple(params))
    return _as_int((row or {}).get("qty"))


def _pullable_qty(row: dict[str, Any]) -> int:
    group_id = row.get("split_group_id")
    if not group_id:
        return 0
    siblings = fetch_all(
        f"""
        SELECT {_DETAIL_COLUMNS}
        FROM order_details
        WHERE split_group_id=%s AND id<>%s
        """,
        (int(group_id), int(row["id"])),
    )
    total = 0
    for sibling in siblings:
        if _can_pull(sibling):
            total += _as_int(sibling.get("quantity"))
    return total


def _can_pull(row: dict[str, Any]) -> bool:
    if not _heat_empty(row):
        return False
    if _positive_reservation_count(int(row["id"])):
        return False
    if _detail_has_shipping(int(row["id"])):
        return False
    if _has_certificate(int(row["id"])):
        return False
    return True


def _positive_reservation_count(order_detail_id: int) -> int:
    row = fetch_one(
        """
        SELECT COUNT(*) AS cnt
        FROM dalian_shipping_reservations
        WHERE order_detail_id=%s AND planned_qty > 0
        """,
        (int(order_detail_id),),
    )
    return _as_int((row or {}).get("cnt"))


def _detail_has_shipping(order_detail_id: int) -> bool:
    row = fetch_one(
        """
        SELECT d.id
        FROM dalian_shipping_details d
        INNER JOIN dalian_shipping_reservations r ON r.id = d.reservation_id
        WHERE r.order_detail_id=%s
        LIMIT 1
        """,
        (int(order_detail_id),),
    )
    return bool(row)


def _has_certificate(order_detail_id: int) -> bool:
    snap = fetch_one(
        "SELECT id FROM qc_certificate_snapshots WHERE order_detail_id=%s LIMIT 1",
        (int(order_detail_id),),
    )
    if snap:
        return True
    allocation = fetch_one(
        "SELECT id FROM dalian_certificate_allocations WHERE order_detail_id=%s LIMIT 1",
        (int(order_detail_id),),
    )
    return bool(allocation)


def _align_with_cursor(
    cursor,
    order_detail_id: int,
    target_qty: int,
    user: CurrentUser,
    *,
    remove_if_zero: bool,
) -> dict[str, Any]:
    cursor.execute(
        f"SELECT {_DETAIL_COLUMNS} FROM order_details WHERE id=%s FOR UPDATE",
        (int(order_detail_id),),
    )
    source = cursor.fetchone()
    if not source:
        raise HTTPException(status_code=404, detail="未找到订单明细")
    current_qty = _as_int(source.get("quantity"))
    result: dict[str, Any] = {
        "changed": False,
        "remainder_created": False,
        "remainder_qty": None,
        "rows": [],
        "before_map": {},
        "deleted_ids": [],
        "logs": [],
    }
    if target_qty == current_qty:
        return result
    if _detail_has_shipping(int(source["id"])):
        raise HTTPException(status_code=400, detail="已有发货明细，不能自动调整数量")
    if target_qty < current_qty and _has_certificate(int(source["id"])):
        raise HTTPException(status_code=400, detail="该明细已生成质检书，不能自动调整数量")

    before_source = _typing_before(source)
    if target_qty > current_qty:
        _pull_from_siblings(cursor, source, target_qty - current_qty, user, result)
    elif target_qty == 0 and remove_if_zero:
        if not _merge_away(cursor, source, user, result):
            return result
    else:
        _release_to_remainder(cursor, source, current_qty - target_qty, user, result)
    source = _reload_locked(cursor, int(source["id"]))
    _set_quantity(cursor, source, target_qty, user, result["logs"])
    result["before_map"][int(source["id"])] = before_source
    result["rows"].append(_typing_after(source, target_qty))
    if target_qty == 0 and remove_if_zero and not _has_certificate(int(source["id"])):
        cursor.execute("DELETE FROM order_details WHERE id=%s", (int(source["id"]),))
        result["deleted_ids"].append(int(source["id"]))
        result["rows"] = [row for row in result["rows"] if int(row["id"]) != int(source["id"])]
        result["before_map"].pop(int(source["id"]), None)
        result["logs"].append(
            {
                "action": "delete",
                "row_id": int(source["id"]),
                "order_no": _norm(source.get("order_no")),
                "material_no": _norm(source.get("material_no")),
                "change_summary": {"op": "split_remove_empty", "quantity_before": current_qty},
            }
        )
    _assert_group_total(cursor, source)
    result["changed"] = True
    return result


def _pull_from_siblings(cursor, source: dict[str, Any], need: int, user: CurrentUser, result: dict[str, Any]) -> None:
    group_id = source.get("split_group_id")
    if not group_id:
        raise HTTPException(status_code=400, detail=f"预定数量超过本行数量 { _as_int(source.get('quantity')) }，没有可扣的余量")
    cursor.execute(
        f"""
        SELECT {_DETAIL_COLUMNS}
        FROM order_details
        WHERE split_group_id=%s AND id<>%s
        ORDER BY id
        FOR UPDATE
        """,
        (int(group_id), int(source["id"])),
    )
    siblings = list(cursor.fetchall())
    left = need
    for sibling in siblings:
        if left <= 0:
            break
        if not _can_pull(sibling):
            continue
        have = _as_int(sibling.get("quantity"))
        take = min(have, left)
        if take <= 0:
            continue
        result["before_map"][int(sibling["id"])] = _typing_before(sibling)
        next_qty = have - take
        _set_quantity(cursor, sibling, next_qty, user, result["logs"])
        if next_qty == 0 and not _has_certificate(int(sibling["id"])):
            cursor.execute("DELETE FROM order_details WHERE id=%s", (int(sibling["id"]),))
            result["deleted_ids"].append(int(sibling["id"]))
            result["logs"].append(
                {
                    "action": "delete",
                    "row_id": int(sibling["id"]),
                    "order_no": _norm(sibling.get("order_no")),
                    "material_no": _norm(sibling.get("material_no")),
                    "change_summary": {"op": "split_consume_remainder", "quantity_before": have},
                }
            )
        else:
            result["rows"].append(_typing_after(sibling, next_qty))
        left -= take
    if left > 0:
        raise HTTPException(
            status_code=400,
            detail="余量行已填写炉号或已有质检书，不能自动调入。请先在订单查询中处理炉号",
        )


def _merge_away(cursor, source: dict[str, Any], user: CurrentUser, result: dict[str, Any]) -> bool:
    """删除预定时，把本行数量并进同组未预定余量。没有余量则保留本行，不拆出新行。"""
    group_id = source.get("split_group_id")
    if not group_id:
        return False
    cursor.execute(
        f"""
        SELECT {_DETAIL_COLUMNS}
        FROM order_details
        WHERE split_group_id=%s AND id<>%s
        ORDER BY id DESC
        FOR UPDATE
        """,
        (int(group_id), int(source["id"])),
    )
    siblings = list(cursor.fetchall())
    unreserved = [row for row in siblings if not _positive_reservation_count(int(row["id"]))]
    if not unreserved:
        return False
    incompatible = [row for row in unreserved if not _heats_compatible(source, row)]
    if incompatible:
        raise HTTPException(
            status_code=400,
            detail="炉号或热处理批号已不同，不能自动并回。请先在订单查询中把炉号改成一致后再调整预定数量",
        )
    target = unreserved[0]
    release = _as_int(source.get("quantity"))
    result["before_map"][int(target["id"])] = _typing_before(target)
    next_qty = _as_int(target.get("quantity")) + release
    _set_quantity(cursor, target, next_qty, user, result["logs"])
    result["rows"].append(_typing_after(target, next_qty))
    result["remainder_qty"] = next_qty
    return True


def _release_to_remainder(cursor, source: dict[str, Any], release: int, user: CurrentUser, result: dict[str, Any]) -> None:
    group_id = _ensure_group(cursor, source, user)
    source["split_group_id"] = group_id
    cursor.execute(
        f"""
        SELECT {_DETAIL_COLUMNS}
        FROM order_details
        WHERE split_group_id=%s AND id<>%s
        ORDER BY id DESC
        FOR UPDATE
        """,
        (int(group_id), int(source["id"])),
    )
    siblings = list(cursor.fetchall())
    unreserved = [row for row in siblings if not _positive_reservation_count(int(row["id"]))]
    incompatible = [row for row in unreserved if not _heats_compatible(source, row)]
    if incompatible:
        raise HTTPException(
            status_code=400,
            detail="炉号或热处理批号已不同，不能自动并回。请先在订单查询中把炉号改成一致后再调整预定数量",
        )
    target = next((row for row in unreserved if _heats_compatible(source, row)), None)
    if target:
        result["before_map"][int(target["id"])] = _typing_before(target)
        next_qty = _as_int(target.get("quantity")) + release
        _set_quantity(cursor, target, next_qty, user, result["logs"])
        result["rows"].append(_typing_after(target, next_qty))
        result["remainder_qty"] = next_qty
        return
    created = _insert_copy(cursor, source, release, user, result["logs"])
    result["remainder_created"] = True
    result["remainder_qty"] = release
    result["rows"].append(created)


def _ensure_group(cursor, source: dict[str, Any], user: CurrentUser) -> int:
    if source.get("split_group_id"):
        return int(source["split_group_id"])
    group_id = int(source["id"])
    contract_qty = _as_int(source.get("contract_qty") or source.get("quantity"))
    cursor.execute(
        """
        UPDATE order_details
        SET split_group_id=%s, contract_qty=%s, updated_by=%s, updated_by_user_id=%s, updated_at=NOW()
        WHERE id=%s
        """,
        (group_id, contract_qty, *update_audit_values(user), group_id),
    )
    source["split_group_id"] = group_id
    source["contract_qty"] = contract_qty
    return group_id


def _set_quantity(cursor, row: dict[str, Any], qty: int, user: CurrentUser, logs: list[dict[str, Any]]) -> None:
    total = _total_weight(row.get("unit_weight"), qty)
    cursor.execute(
        """
        UPDATE order_details
        SET quantity=%s, total_weight=%s, updated_by=%s, updated_by_user_id=%s, updated_at=NOW()
        WHERE id=%s
        """,
        (int(qty), total, *update_audit_values(user), int(row["id"])),
    )
    before_qty = _as_int(row.get("quantity"))
    if before_qty != int(qty):
        logs.append(
            {
                "action": "update",
                "row_id": int(row["id"]),
                "order_no": _norm(row.get("order_no")),
                "material_no": _norm(row.get("material_no")),
                "change_summary": {
                    "op": "dalian_split_qty",
                    "fields": {"quantity": {"before": before_qty, "after": int(qty)}},
                },
            }
        )
    row["quantity"] = int(qty)
    row["total_weight"] = total


def _insert_copy(cursor, source: dict[str, Any], qty: int, user: CurrentUser, logs: list[dict[str, Any]]) -> dict[str, Any]:
    cursor.execute(
        "SELECT COALESCE(MAX(seq), 0) AS seq FROM order_details WHERE order_no=%s",
        (_norm(source.get("order_no")),),
    )
    seq_row = cursor.fetchone() or {}
    seq = _as_int(seq_row.get("seq")) + 1
    total = _total_weight(source.get("unit_weight"), qty)
    group_id = int(source.get("split_group_id") or source["id"])
    contract_qty = _as_int(source.get("contract_qty") or source.get("quantity"))
    cursor.execute(
        """
        INSERT INTO order_details (
            order_no, customer, factory_order_no, seq, item_no, name, drawing_no, material_no,
            spec_model, spec, standard, material, quantity, unit_weight, total_weight,
            agreement_price, product_unit_price, remark1, remark2, heat_no, heat_treatment_batch_no,
            order_status, doc_status, upload_type, material_mode, started_at, finished_at,
            split_group_id, contract_qty, created_by, created_by_user_id, updated_by, updated_by_user_id
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s
        )
        """,
        (
            source.get("order_no"),
            source.get("customer"),
            source.get("factory_order_no"),
            seq,
            source.get("item_no"),
            source.get("name"),
            source.get("drawing_no"),
            source.get("material_no"),
            source.get("spec_model"),
            source.get("spec"),
            source.get("standard"),
            source.get("material"),
            int(qty),
            source.get("unit_weight") or 0,
            total,
            source.get("agreement_price") or 0,
            source.get("product_unit_price") or 0,
            source.get("remark1"),
            source.get("remark2"),
            source.get("heat_no"),
            source.get("heat_treatment_batch_no"),
            source.get("order_status") or "开始",
            source.get("doc_status") or "",
            source.get("upload_type"),
            source.get("material_mode"),
            source.get("started_at"),
            source.get("finished_at"),
            group_id,
            contract_qty,
            *create_audit_values(user),
        ),
    )
    new_id = int(cursor.lastrowid or 0)
    created = {
        "id": new_id,
        "order_no": source.get("order_no"),
        "quantity": int(qty),
        "heat_no": source.get("heat_no") or "",
        "heat_treatment_batch_no": source.get("heat_treatment_batch_no") or "",
    }
    logs.append(
        {
            "action": "create",
            "row_id": new_id,
            "order_no": _norm(source.get("order_no")),
            "material_no": _norm(source.get("material_no")),
            "change_summary": {"op": "dalian_split_remainder", "based_on_id": int(source["id"]), "quantity": int(qty)},
        }
    )
    return created


def _reload_locked(cursor, order_detail_id: int) -> dict[str, Any]:
    cursor.execute(f"SELECT {_DETAIL_COLUMNS} FROM order_details WHERE id=%s FOR UPDATE", (int(order_detail_id),))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="未找到订单明细")
    return row


def _assert_group_total(cursor, source: dict[str, Any]) -> None:
    group_id = source.get("split_group_id")
    contract_qty = source.get("contract_qty")
    if not group_id or contract_qty is None:
        return
    cursor.execute(
        "SELECT COALESCE(SUM(quantity), 0) AS qty FROM order_details WHERE split_group_id=%s",
        (int(group_id),),
    )
    row = cursor.fetchone() or {}
    if _as_int(row.get("qty")) != _as_int(contract_qty):
        raise HTTPException(status_code=400, detail="拆分后数量合计与合同数量不一致，已取消本次预定")


def _typing_before(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "order_no": row.get("order_no"),
        "quantity": _as_int(row.get("quantity")),
        "heat_no": row.get("heat_no") or "",
        "heat_treatment_batch_no": row.get("heat_treatment_batch_no") or "",
    }


def _typing_after(row: dict[str, Any], qty: int) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "order_no": row.get("order_no"),
        "quantity": int(qty),
        "heat_no": row.get("heat_no") or "",
        "heat_treatment_batch_no": row.get("heat_treatment_batch_no") or "",
    }


def _after_change(result: dict[str, Any], user: CurrentUser) -> None:
    if not result.get("changed"):
        return
    for item in result.get("logs") or []:
        write_op_log(user=user, **item)
    _clear_order_cache()
    rows = result.get("rows") or []
    if not rows:
        return
    from app.services.typing_notice import sync_typing_after_experiment_change

    sync_typing_after_experiment_change(rows, result.get("before_map") or {}, user)


def _clear_order_cache() -> None:
    from app.api.v1 import workflow as workflow_api

    workflow_api.order_details.clear()


def _plan_migration(detail_id: int) -> dict[str, Any] | None:
    row = _load_row(detail_id)
    if row.get("split_group_id"):
        return None
    reservations = fetch_all(
        """
        SELECT id, planned_arrival_date, planned_qty
        FROM dalian_shipping_reservations
        WHERE order_detail_id=%s AND planned_qty > 0
        ORDER BY planned_arrival_date, id
        """,
        (int(detail_id),),
    )
    if not reservations:
        return None
    original_qty = _as_int(row.get("quantity"))
    planned = [_as_int(item.get("planned_qty")) for item in reservations]
    if len(reservations) == 1 and planned[0] == original_qty:
        return None
    if sum(planned) > original_qty:
        return {
            "order_detail_id": detail_id,
            "order_no": row.get("order_no"),
            "skip": True,
            "reason": "预定数量合计超过订单数量",
            "quantity": original_qty,
            "planned": planned,
        }
    has_certificate = _has_certificate(detail_id)
    return {
        "order_detail_id": detail_id,
        "order_no": row.get("order_no"),
        "material_no": row.get("material_no"),
        "skip": False,
        "quantity_before": original_qty,
        "kept_qty": planned[0],
        "extra_reservation_ids": [int(item["id"]) for item in reservations[1:]],
        "remainder_qty": original_qty - sum(planned),
        "certificate_unchanged": has_certificate and planned[0] != original_qty,
    }


def _apply_migration(detail_id: int, entry: dict[str, Any]) -> None:
    user = CurrentUser(
        id=0,
        name="迁移",
        username="migrate",
        role_id=0,
        role_code="system",
        role_name_zh="迁移",
    )
    logs: list[dict[str, Any]] = []
    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                f"SELECT {_DETAIL_COLUMNS} FROM order_details WHERE id=%s FOR UPDATE",
                (int(detail_id),),
            )
            source = cursor.fetchone()
            if not source or source.get("split_group_id"):
                cursor.close()
                return
            cursor.execute(
                """
                SELECT id, planned_qty, unit_weight
                FROM dalian_shipping_reservations
                WHERE order_detail_id=%s AND planned_qty > 0
                ORDER BY planned_arrival_date, id
                FOR UPDATE
                """,
                (int(detail_id),),
            )
            reservations = list(cursor.fetchall())
            if not reservations:
                cursor.close()
                return
            original_qty = _as_int(source.get("quantity"))
            _ensure_group(cursor, source, user)
            first_qty = _as_int(reservations[0].get("planned_qty"))
            _set_quantity(cursor, source, first_qty, user, logs)
            _sync_reservation_qty(cursor, reservations[0], detail_id, first_qty)
            for reservation in reservations[1:]:
                part_qty = _as_int(reservation.get("planned_qty"))
                created = _insert_copy(cursor, source, part_qty, user, logs)
                _sync_reservation_qty(cursor, reservation, int(created["id"]), part_qty)
            remainder = original_qty - sum(_as_int(item.get("planned_qty")) for item in reservations)
            if remainder > 0:
                _insert_copy(cursor, source, remainder, user, logs)
        except Exception:
            conn.rollback()
            cursor.close()
            raise
        cursor.close()
    for item in logs:
        write_op_log(user=user, **item)
    _ = entry


def _sync_reservation_qty(cursor, reservation: dict[str, Any], order_detail_id: int, qty: int) -> None:
    unit = float(reservation.get("unit_weight") or 0)
    cursor.execute(
        """
        UPDATE dalian_shipping_reservations
        SET order_detail_id=%s, order_qty=%s, total_weight=%s, updated_at=NOW()
        WHERE id=%s
        """,
        (int(order_detail_id), int(qty), round(unit * int(qty), 2), int(reservation["id"])),
    )
