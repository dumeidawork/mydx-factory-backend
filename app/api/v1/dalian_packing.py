"""大连手动/自动装箱：按预定到货日读取发货预定，包装序号写入大连发货明细。"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.api.v1.dalian_shipping_reservations import _ensure_tables as _ensure_reservations, _parse_date
from app.core.database import execute, fetch_all, fetch_one
from app.api.v1.france_auto_packing_qty_maps import DALIAN_MAP_TABLE, ensure_dalian_qty_maps
from app.services.france_auto_packing import _dn_number, build_boxes, enrich_items, flatten_rows

router = APIRouter(prefix="/dalian-packing", tags=["大连装箱"])
UserDep = Annotated[CurrentUser, Depends(get_current_user)]

_READY = False

CREATE_LOG_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS dalian_packing_operation_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    planned_arrival_date DATE NULL COMMENT '预定到货日期',
    reservation_id BIGINT NULL COMMENT '发货预定行ID',
    shipping_detail_id BIGINT NULL COMMENT '大连发货明细ID',
    action VARCHAR(32) NOT NULL COMMENT 'preview/blocked/create/update/delete',
    operator_user_id INT NULL COMMENT '操作人用户ID',
    operator_name VARCHAR(64) NOT NULL DEFAULT '' COMMENT '操作人',
    operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '操作时间',
    change_summary TEXT NULL COMMENT '变更内容 JSON',
    INDEX idx_dalian_pack_log_date (planned_arrival_date),
    INDEX idx_dalian_pack_log_reservation (reservation_id),
    INDEX idx_dalian_pack_log_detail (shipping_detail_id),
    INDEX idx_dalian_pack_log_action (action),
    INDEX idx_dalian_pack_log_time (operated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大连装箱操作日志'
"""

_DETAIL_ALTERS = (
    ("created_by_user_id", "ALTER TABLE dalian_shipping_details ADD COLUMN created_by_user_id INT NULL COMMENT '上传者用户ID' AFTER created_by"),
    ("updated_by", "ALTER TABLE dalian_shipping_details ADD COLUMN updated_by VARCHAR(64) NULL COMMENT '更改者' AFTER created_by_user_id"),
    ("updated_by_user_id", "ALTER TABLE dalian_shipping_details ADD COLUMN updated_by_user_id INT NULL COMMENT '更改者用户ID' AFTER updated_by"),
    ("updated_at", "ALTER TABLE dalian_shipping_details ADD COLUMN updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更改时间' AFTER updated_by_user_id"),
    ("reservation_id", "ALTER TABLE dalian_shipping_details ADD COLUMN reservation_id BIGINT NULL COMMENT '发货预定行ID，手工粘贴行为空' AFTER id"),
    ("planned_arrival_date", "ALTER TABLE dalian_shipping_details ADD COLUMN planned_arrival_date DATE NULL COMMENT '预定到货日期' AFTER reservation_id"),
)


class SaveLineIn(BaseModel):
    reservation_id: int
    packing_no: str = ""
    quantity: int = 0


class SaveRequest(BaseModel):
    planned_arrival_date: str
    actual_delivery_date: str
    mode: str = "manual"
    rows: list[SaveLineIn] = Field(default_factory=list)


class PreviewRequest(BaseModel):
    planned_arrival_date: str


class MarkLogRequest(BaseModel):
    planned_arrival_date: str
    reservation_id: int | None = None
    action: str
    change_summary: dict[str, Any] = Field(default_factory=dict)


def _column_exists(table: str, column: str) -> bool:
    row = fetch_one(
        """
        SELECT 1 AS n FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name=%s AND column_name=%s
        LIMIT 1
        """,
        (table, column),
    )
    return bool(row)


def _index_exists(table: str, index_name: str) -> bool:
    row = fetch_one(
        """
        SELECT 1 AS n FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name=%s AND index_name=%s
        LIMIT 1
        """,
        (table, index_name),
    )
    return bool(row)


def _ensure_tables() -> None:
    global _READY
    if _READY:
        return
    _ensure_reservations()
    execute(CREATE_LOG_TABLE_SQL)
    for column, ddl in _DETAIL_ALTERS:
        if not _column_exists("dalian_shipping_details", column):
            execute(ddl)
    if not _index_exists("dalian_shipping_details", "uk_dsd_reservation_packing"):
        execute(
            "ALTER TABLE dalian_shipping_details ADD UNIQUE KEY uk_dsd_reservation_packing (reservation_id, packing_no)"
        )
    if not _index_exists("dalian_shipping_details", "idx_dsd_planned_arrival"):
        execute("ALTER TABLE dalian_shipping_details ADD INDEX idx_dsd_planned_arrival (planned_arrival_date)")
    _READY = True


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return value


def _operator_name(user: CurrentUser) -> str:
    return _norm(user.name) or _norm(user.username) or str(user.id)


def _as_float(value: Any) -> float:
    try:
        if value is None or str(value).strip() == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    try:
        if value is None or str(value).strip() == "":
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _yyyymmdd(day: date) -> str:
    return day.strftime("%Y%m%d")


def _remark(material: str, heat_batch: str) -> str:
    text = _norm(material).upper()
    if ("A105" == text or "A105" in text) and _norm(heat_batch):
        return "绿色"
    return "白色"


def _write_log(
    user: CurrentUser,
    *,
    planned_arrival_date: date | None,
    reservation_id: int | None,
    shipping_detail_id: int | None,
    action: str,
    change_summary: dict[str, Any],
) -> None:
    execute(
        """
        INSERT INTO dalian_packing_operation_logs
            (planned_arrival_date, reservation_id, shipping_detail_id, action,
             operator_user_id, operator_name, change_summary)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            planned_arrival_date,
            reservation_id,
            shipping_detail_id,
            action,
            user.id,
            _operator_name(user),
            json.dumps(change_summary, ensure_ascii=False, default=str),
        ),
    )


def _load_sources(day: date) -> list[dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT r.id AS reservation_id, r.order_detail_id, r.order_no, r.factory_order_no, r.part_no,
               r.material_no, r.spec_model, r.material, r.item_no, r.drawing_no, r.unit_weight,
               r.planned_qty, r.remark,
               d.product_unit_price, d.heat_no, d.heat_treatment_batch_no, d.spec, d.standard
        FROM dalian_shipping_reservations r
        LEFT JOIN order_details d ON d.id = r.order_detail_id
        WHERE r.planned_arrival_date=%s AND r.planned_qty>0
        ORDER BY r.order_no, r.id
        """,
        (day,),
    )
    sources: list[dict[str, Any]] = []
    for row in rows:
        spec_model = _norm(row.get("spec_model"))
        order_spec = _norm(row.get("spec"))
        dn_spec = order_spec if _dn_number(order_spec) else spec_model or order_spec
        material = _norm(row.get("material"))
        heat_batch = _norm(row.get("heat_treatment_batch_no"))
        unit_weight = round(_as_float(row.get("unit_weight")), 3)
        quantity = _as_int(row.get("planned_qty"))
        price = row.get("product_unit_price")
        sources.append(
            {
                "reservation_id": int(row["reservation_id"]),
                "order_detail_id": int(row["order_detail_id"]) if row.get("order_detail_id") is not None else None,
                "order_no": _norm(row.get("order_no")),
                "factory_order_no": _norm(row.get("factory_order_no")),
                "part_no": _norm(row.get("part_no")),
                "material_no": _norm(row.get("material_no")),
                "spec_model": spec_model,
                "material": material,
                "item_no": "" if row.get("item_no") is None else str(row.get("item_no")),
                "drawing_no": _norm(row.get("drawing_no")),
                "unit_weight": unit_weight,
                "quantity": quantity,
                "remark": _norm(row.get("remark")),
                "unit_price": None if price is None else round(_as_float(price), 2),
                "heat_no": _norm(row.get("heat_no")),
                "heat_treatment_batch_no": heat_batch,
                "shipping_mark_remark": _remark(material, heat_batch),
                "dn_spec": dn_spec,
                "model": _norm(row.get("standard")),
                "buyer": "",
            }
        )
    return sources


def _saved_by_reservation(reservation_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not reservation_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(reservation_ids))
    rows = fetch_all(
        f"""
        SELECT * FROM dalian_shipping_details
        WHERE reservation_id IN ({placeholders})
        ORDER BY reservation_id, id
        """,
        tuple(reservation_ids),
    )
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["reservation_id"]), []).append(row)
    return grouped


def _line_from_source(
    source: dict[str, Any],
    *,
    packing_no: str = "",
    quantity: int | None = None,
    shipping_detail_id: int | None = None,
    actual_delivery_date: str = "",
    warnings: list[str] | None = None,
    blocking: list[str] | None = None,
) -> dict[str, Any]:
    qty = source["quantity"] if quantity is None else int(quantity)
    unit_weight = float(source["unit_weight"])
    return {
        "reservation_id": source["reservation_id"],
        "order_detail_id": source["order_detail_id"],
        "shipping_detail_id": shipping_detail_id,
        "order_no": source["order_no"],
        "factory_order_no": source["factory_order_no"],
        "part_no": source["part_no"],
        "material_no": source["material_no"],
        "spec_model": source["spec_model"],
        "material": source["material"],
        "item_no": source["item_no"],
        "drawing_no": source["drawing_no"],
        "unit_weight": unit_weight,
        "quantity": qty,
        "total_weight": round(unit_weight * qty, 3),
        "remark": source["remark"],
        "unit_price": source["unit_price"],
        "heat_no": source["heat_no"],
        "heat_treatment_batch_no": source["heat_treatment_batch_no"],
        "shipping_mark_remark": source["shipping_mark_remark"],
        "buyer": "",
        "packing_no": packing_no,
        "actual_delivery_date": actual_delivery_date,
        "planned_qty": source["quantity"],
        "warnings": warnings or [],
        "blocking": blocking or [],
    }


def _lines_from_saved(sources: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    saved = _saved_by_reservation([item["reservation_id"] for item in sources])
    lines: list[dict[str, Any]] = []
    dates: list[str] = []
    for source in sources:
        existing = saved.get(source["reservation_id"]) or []
        if not existing:
            lines.append(_line_from_source(source))
            continue
        for row in existing:
            delivery = _norm(row.get("delivery_date"))
            if delivery:
                dates.append(delivery)
            lines.append(
                _line_from_source(
                    source,
                    packing_no=_norm(row.get("packing_no")),
                    quantity=_as_int(row.get("quantity")),
                    shipping_detail_id=int(row["id"]),
                    actual_delivery_date=delivery,
                )
            )
    shared = dates[0] if dates and all(item == dates[0] for item in dates) else ""
    return lines, shared


def _snapshot(line: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "packing_no",
        "actual_delivery_date",
        "quantity",
        "unit_weight",
        "total_weight",
        "unit_price",
        "order_no",
        "factory_order_no",
        "item_no",
        "drawing_no",
        "material_no",
        "spec_model",
        "material",
        "heat_no",
        "heat_treatment_batch_no",
        "shipping_mark_remark",
        "remark",
        "ap1_material_no",
        "r3p_material_no",
    )
    return {key: line.get(key) for key in keys}


def _persist_line(source: dict[str, Any], packing_no: str, quantity: int, delivery: str) -> dict[str, Any]:
    line = _line_from_source(source, packing_no=packing_no, quantity=quantity, actual_delivery_date=delivery)
    line["ap1_material_no"] = source["part_no"]
    line["r3p_material_no"] = source["material_no"]
    return line


def _values(line: dict[str, Any], day: date, user: CurrentUser) -> dict[str, Any]:
    return {
        "reservation_id": line["reservation_id"],
        "planned_arrival_date": day,
        "delivery_date": line["actual_delivery_date"],
        "packing_no": _norm(line["packing_no"]),
        "drawing_no": line["drawing_no"],
        "ap1_material_no": line["part_no"],
        "r3p_material_no": line["material_no"],
        "spec_model": line["spec_model"],
        "material": line["material"],
        "quantity": int(line["quantity"]),
        "unit_weight": line["unit_weight"],
        "total_weight": line["total_weight"],
        "order_no": line["order_no"],
        "factory_order_no": line["factory_order_no"],
        "item_no": line["item_no"] or "",
        "unit_price": line["unit_price"],
        "remark": line["remark"],
        "buyer": "",
        "heat_no": line["heat_no"],
        "heat_treatment_batch_no": line["heat_treatment_batch_no"],
        "shipping_mark_remark": line["shipping_mark_remark"],
        "operator": _operator_name(user),
        "operator_id": user.id,
    }


def _stored_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    price = row.get("unit_price")
    return {
        "packing_no": _norm(row.get("packing_no")),
        "actual_delivery_date": _norm(row.get("delivery_date")),
        "quantity": _as_int(row.get("quantity")),
        "unit_weight": round(_as_float(row.get("unit_weight")), 3),
        "total_weight": round(_as_float(row.get("total_weight")), 3),
        "unit_price": None if price is None else round(_as_float(price), 2),
        "order_no": _norm(row.get("order_no")),
        "factory_order_no": _norm(row.get("factory_order_no")),
        "item_no": _norm(row.get("item_no")),
        "drawing_no": _norm(row.get("drawing_no")),
        "material_no": _norm(row.get("r3p_material_no")),
        "spec_model": _norm(row.get("spec_model")),
        "material": _norm(row.get("material")),
        "heat_no": _norm(row.get("heat_no")),
        "heat_treatment_batch_no": _norm(row.get("heat_treatment_batch_no")),
        "shipping_mark_remark": _norm(row.get("shipping_mark_remark")),
        "remark": _norm(row.get("remark")),
        "ap1_material_no": _norm(row.get("ap1_material_no")),
        "r3p_material_no": _norm(row.get("r3p_material_no")),
    }


def _desired_snapshot(values: dict[str, Any]) -> dict[str, Any]:
    return {
        "packing_no": values["packing_no"],
        "actual_delivery_date": values["delivery_date"],
        "quantity": values["quantity"],
        "unit_weight": round(float(values["unit_weight"]), 3),
        "total_weight": round(float(values["total_weight"]), 3),
        "unit_price": values["unit_price"],
        "order_no": values["order_no"],
        "factory_order_no": values["factory_order_no"],
        "item_no": values["item_no"],
        "drawing_no": values["drawing_no"],
        "material_no": values["r3p_material_no"],
        "spec_model": values["spec_model"],
        "material": values["material"],
        "heat_no": values["heat_no"],
        "heat_treatment_batch_no": values["heat_treatment_batch_no"],
        "shipping_mark_remark": values["shipping_mark_remark"],
        "remark": values["remark"],
        "ap1_material_no": values["ap1_material_no"],
        "r3p_material_no": values["r3p_material_no"],
    }


def _insert_detail(values: dict[str, Any]) -> int:
    new_id = execute(
        """
        INSERT INTO dalian_shipping_details (
            reservation_id, planned_arrival_date, delivery_date, packing_no, drawing_no,
            ap1_material_no, r3p_material_no, spec_model, material, quantity, unit_weight, total_weight,
            order_no, factory_order_no, item_no, unit_price, remark, buyer, heat_no,
            heat_treatment_batch_no, shipping_mark_remark, created_by, created_by_user_id,
            updated_by, updated_by_user_id
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s
        )
        """,
        (
            values["reservation_id"],
            values["planned_arrival_date"],
            values["delivery_date"],
            values["packing_no"],
            values["drawing_no"] or None,
            values["ap1_material_no"] or None,
            values["r3p_material_no"] or None,
            values["spec_model"] or None,
            values["material"] or None,
            values["quantity"],
            values["unit_weight"],
            values["total_weight"],
            values["order_no"],
            values["factory_order_no"] or None,
            values["item_no"],
            values["unit_price"],
            values["remark"] or None,
            None,
            values["heat_no"] or None,
            values["heat_treatment_batch_no"] or None,
            values["shipping_mark_remark"] or None,
            values["operator"][:50],
            values["operator_id"],
            values["operator"][:64],
            values["operator_id"],
        ),
    )
    return int(new_id)


def _update_detail(row_id: int, values: dict[str, Any]) -> None:
    execute(
        """
        UPDATE dalian_shipping_details
        SET planned_arrival_date=%s, delivery_date=%s, packing_no=%s, drawing_no=%s,
            ap1_material_no=%s, r3p_material_no=%s, spec_model=%s, material=%s,
            quantity=%s, unit_weight=%s, total_weight=%s, order_no=%s, factory_order_no=%s,
            item_no=%s, unit_price=%s, remark=%s, buyer=%s, heat_no=%s,
            heat_treatment_batch_no=%s, shipping_mark_remark=%s,
            updated_by=%s, updated_by_user_id=%s, updated_at=NOW()
        WHERE id=%s
        """,
        (
            values["planned_arrival_date"],
            values["delivery_date"],
            values["packing_no"],
            values["drawing_no"] or None,
            values["ap1_material_no"] or None,
            values["r3p_material_no"] or None,
            values["spec_model"] or None,
            values["material"] or None,
            values["quantity"],
            values["unit_weight"],
            values["total_weight"],
            values["order_no"],
            values["factory_order_no"] or None,
            values["item_no"],
            values["unit_price"],
            values["remark"] or None,
            None,
            values["heat_no"] or None,
            values["heat_treatment_batch_no"] or None,
            values["shipping_mark_remark"] or None,
            values["operator"][:64],
            values["operator_id"],
            row_id,
        ),
    )


def _analyze(sources: list[dict[str, Any]]) -> tuple[bool, list[str], list[dict[str, Any]]]:
    raw_items = [
        {
            "id": source["reservation_id"],
            "pos": _as_int(source["item_no"]),
            "material_no": source["material_no"],
            "spec": source["dn_spec"],
            "model": source["model"],
            "material": source["material"],
            "drawing_no": source["drawing_no"],
            "quantity": source["quantity"],
            "unit_weight": source["unit_weight"],
        }
        for source in sources
    ]
    ensure_dalian_qty_maps()
    items = enrich_items(raw_items, qty_map_table=DALIAN_MAP_TABLE)
    messages: list[str] = []
    for item in items:
        if not _dn_number(item.dn) and "缺 DN" not in item.blocking:
            item.blocking.append("缺 DN")
        for text in item.blocking:
            label = f"{item.material_no or item.item_id}：{text}"
            if label not in messages:
                messages.append(label)
    blocked = bool(messages)
    if blocked:
        return True, messages, []
    boxes = build_boxes(items)
    flat = flatten_rows(boxes, "", 0)
    return False, messages, flat


def _preview_lines(sources: list[dict[str, Any]]) -> tuple[bool, list[str], list[dict[str, Any]]]:
    blocked, messages, flat = _analyze(sources)
    source_map = {item["reservation_id"]: item for item in sources}
    if blocked:
        saved_lines, _shared = _lines_from_saved(sources)
        enriched = {item.item_id: item for item in enrich_items([
            {
                "id": source["reservation_id"],
                "pos": _as_int(source["item_no"]),
                "material_no": source["material_no"],
                "spec": source["dn_spec"],
                "model": source["model"],
                "material": source["material"],
                "drawing_no": source["drawing_no"],
                "quantity": source["quantity"],
                "unit_weight": source["unit_weight"],
            }
            for source in sources
        ], qty_map_table=DALIAN_MAP_TABLE)}
        for line in saved_lines:
            item = enriched.get(line["reservation_id"])
            if not item:
                continue
            blocking = list(item.blocking)
            if not _dn_number(item.dn) and "缺 DN" not in blocking:
                blocking.append("缺 DN")
            line["blocking"] = blocking
            line["warnings"] = list(item.warnings)
        return True, messages, saved_lines
    lines: list[dict[str, Any]] = []
    for row in flat:
        source = source_map.get(int(row.get("item_id") or 0))
        if not source:
            continue
        lines.append(
            _line_from_source(
                source,
                packing_no=str(int(row.get("box_no") or 0)),
                quantity=_as_int(row.get("quantity")),
                warnings=list(row.get("warnings") or []),
                blocking=list(row.get("blocking") or []),
            )
        )
    merged: dict[tuple[int, str], dict[str, Any]] = {}
    order: list[tuple[int, str]] = []
    for line in lines:
        key = (int(line["reservation_id"]), _norm(line["packing_no"]))
        current = merged.get(key)
        if not current:
            merged[key] = line
            order.append(key)
            continue
        current["quantity"] = int(current["quantity"]) + int(line["quantity"])
        current["total_weight"] = round(float(current["unit_weight"]) * int(current["quantity"]), 3)
        current["warnings"] = list(dict.fromkeys([*(current.get("warnings") or []), *(line.get("warnings") or [])]))
        current["blocking"] = list(dict.fromkeys([*(current.get("blocking") or []), *(line.get("blocking") or [])]))
    return False, messages, [merged[key] for key in order]


@router.get("/load")
def load_packing(planned_arrival_date: str, _user: UserDep):
    _ensure_tables()
    day = _parse_date(planned_arrival_date)
    sources = _load_sources(day)
    lines, shared_date = _lines_from_saved(sources)
    return {
        "status": "success",
        "planned_arrival_date": day.strftime("%Y-%m-%d"),
        "actual_delivery_date": shared_date,
        "rows": lines,
    }


@router.post("/preview")
def preview_packing(req: PreviewRequest, user: UserDep):
    _ensure_tables()
    day = _parse_date(req.planned_arrival_date)
    sources = _load_sources(day)
    if not sources:
        raise HTTPException(status_code=400, detail="该日没有预定数量大于 0 的发货预定")
    blocked, messages, lines = _preview_lines(sources)
    _write_log(
        user,
        planned_arrival_date=day,
        reservation_id=None,
        shipping_detail_id=None,
        action="blocked" if blocked else "preview",
        change_summary={
            "op": "blocked" if blocked else "preview",
            "messages": messages,
            "line_count": len(lines),
            "reservation_ids": [item["reservation_id"] for item in sources],
        },
    )
    return {
        "status": "blocked" if blocked else "success",
        "blocked": blocked,
        "messages": messages,
        "planned_arrival_date": day.strftime("%Y-%m-%d"),
        "rows": lines,
    }


@router.post("/save")
def save_packing(req: SaveRequest, user: UserDep):
    _ensure_tables()
    mode = _norm(req.mode) or "manual"
    if mode not in {"manual", "auto"}:
        raise HTTPException(status_code=400, detail="保存模式无效")
    day = _parse_date(req.planned_arrival_date)
    delivery = _parse_date(req.actual_delivery_date, "实际发货日期")
    delivery_text = _yyyymmdd(delivery)
    sources = _load_sources(day)
    if not sources:
        raise HTTPException(status_code=400, detail="该日没有预定数量大于 0 的发货预定")
    source_map = {item["reservation_id"]: item for item in sources}
    if mode == "auto":
        blocked, messages, _lines = _analyze(sources)
        if blocked:
            _write_log(
                user,
                planned_arrival_date=day,
                reservation_id=None,
                shipping_detail_id=None,
                action="blocked",
                change_summary={"op": "blocked", "messages": messages, "at": "save"},
            )
            raise HTTPException(status_code=400, detail="缺 DN 或每托数量，不能写入发货明细：" + "；".join(messages))
    if not req.rows:
        raise HTTPException(status_code=400, detail="没有可保存的明细")

    grouped: dict[int, list[SaveLineIn]] = {}
    for line in req.rows:
        if line.reservation_id not in source_map:
            raise HTTPException(status_code=400, detail=f"预定行 {line.reservation_id} 不在该到货日的待装箱明细中")
        grouped.setdefault(line.reservation_id, []).append(line)
    missing = [item["reservation_id"] for item in sources if item["reservation_id"] not in grouped]
    if missing:
        raise HTTPException(status_code=400, detail="保存须包含该日全部待装箱预定行")

    desired: list[dict[str, Any]] = []
    for reservation_id, lines in grouped.items():
        source = source_map[reservation_id]
        if len(lines) == 1 and mode == "manual":
            packing_no = _norm(lines[0].packing_no)
            quantity = _as_int(lines[0].quantity)
            if quantity <= 0:
                quantity = source["quantity"]
            if quantity != source["quantity"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"预定行 {reservation_id} 未拆分时，数量须等于预定数量 {source['quantity']}",
                )
            desired.append(_persist_line(source, packing_no, quantity, delivery_text))
            continue
        parsed: list[tuple[str, int]] = []
        seen: set[str] = set()
        total = 0
        for line in lines:
            packing_no = _norm(line.packing_no)
            if not packing_no:
                raise HTTPException(status_code=400, detail="分成多行时包装序号不能为空")
            if packing_no in seen:
                raise HTTPException(status_code=400, detail=f"预定行 {reservation_id} 的包装序号 {packing_no} 重复")
            seen.add(packing_no)
            quantity = _as_int(line.quantity)
            if quantity <= 0:
                raise HTTPException(status_code=400, detail=f"预定行 {reservation_id} 的数量须大于 0")
            total += quantity
            parsed.append((packing_no, quantity))
        if total != source["quantity"]:
            raise HTTPException(
                status_code=400,
                detail=f"预定行 {reservation_id} 的分箱数量之和须等于预定数量 {source['quantity']}",
            )
        for packing_no, quantity in parsed:
            desired.append(_persist_line(source, packing_no, quantity, delivery_text))

    existing = _saved_by_reservation(list(source_map))
    existing_by_key: dict[tuple[int, str], dict[str, Any]] = {}
    for reservation_id, rows in existing.items():
        for row in rows:
            existing_by_key[(reservation_id, _norm(row.get("packing_no")))] = row

    desired_keys = {(int(line["reservation_id"]), _norm(line["packing_no"])) for line in desired}
    for key, row in existing_by_key.items():
        if key in desired_keys:
            continue
        execute("DELETE FROM dalian_shipping_details WHERE id=%s", (int(row["id"]),))
        _write_log(
            user,
            planned_arrival_date=day,
            reservation_id=key[0],
            shipping_detail_id=int(row["id"]),
            action="delete",
            change_summary={"op": "delete", "before": _stored_snapshot(row)},
        )

    saved_ids: list[int] = []
    for line in desired:
        values = _values(line, day, user)
        key = (int(line["reservation_id"]), values["packing_no"])
        current = existing_by_key.get(key)
        after = _desired_snapshot(values)
        if current:
            before = _stored_snapshot(current)
            saved_ids.append(int(current["id"]))
            if before != after:
                _update_detail(int(current["id"]), values)
                _write_log(
                    user,
                    planned_arrival_date=day,
                    reservation_id=key[0],
                    shipping_detail_id=int(current["id"]),
                    action="update",
                    change_summary={"op": "update", "before": before, "after": after},
                )
            continue
        new_id = _insert_detail(values)
        saved_ids.append(new_id)
        _write_log(
            user,
            planned_arrival_date=day,
            reservation_id=key[0],
            shipping_detail_id=new_id,
            action="create",
            change_summary={"op": "create", "after": after},
        )

    _mark_reserved_details_shipped(list(source_map.values()), delivery, user)
    lines, shared_date = _lines_from_saved(sources)
    return {
        "status": "success",
        "planned_arrival_date": day.strftime("%Y-%m-%d"),
        "actual_delivery_date": shared_date or delivery_text,
        "saved_ids": saved_ids,
        "rows": lines,
    }


def _mark_reserved_details_shipped(sources: list[dict[str, Any]], delivery: date, user: CurrentUser) -> None:
    """只把本次装箱对应的订单明细改为发货，余量行保持原状态。"""
    shipped_at = datetime.combine(delivery, datetime.min.time())
    detail_ids = sorted({int(item["order_detail_id"]) for item in sources if item.get("order_detail_id")})
    if not detail_ids:
        return
    from app.api.v1 import workflow
    from app.services.order_detail_audit import update_audit_values, write_order_detail_op_log

    updated_by, updated_by_user_id = update_audit_values(user)
    placeholders = ", ".join(["%s"] * len(detail_ids))
    before_rows = fetch_all(
        f"SELECT id, order_no, material_no, order_status, shipped_at FROM order_details WHERE id IN ({placeholders})",
        tuple(detail_ids),
    )
    execute(
        f"""
        UPDATE order_details
        SET order_status=%s, shipped_at=%s, updated_at=NOW(), updated_by=%s, updated_by_user_id=%s
        WHERE id IN ({placeholders})
        """,
        ("发货", shipped_at, updated_by, updated_by_user_id, *detail_ids),
    )
    for row in before_rows:
        if _norm(row.get("order_status")) == "发货" and row.get("shipped_at"):
            continue
        write_order_detail_op_log(
            action="update",
            user=user,
            row_id=int(row["id"]),
            order_no=_norm(row.get("order_no")),
            material_no=_norm(row.get("material_no")),
            change_summary={
                "op": "ship",
                "before": {"status": _norm(row.get("order_status")), "shipped_at": str(row.get("shipped_at") or "")},
                "after": {"status": "发货", "shipped_at": shipped_at.strftime("%Y-%m-%d %H:%M:%S")},
            },
        )
    workflow.order_details.clear()


@router.post("/log")
def mark_log(req: MarkLogRequest, user: UserDep):
    _ensure_tables()
    if req.action not in {"copy", "delete"}:
        raise HTTPException(status_code=400, detail="不支持的日志动作")
    day = _parse_date(req.planned_arrival_date)
    summary = dict(req.change_summary or {})
    summary.setdefault("op", req.action)
    _write_log(
        user,
        planned_arrival_date=day,
        reservation_id=req.reservation_id,
        shipping_detail_id=None,
        action=req.action,
        change_summary=summary,
    )
    return {"status": "success"}


@router.get("/logs")
def list_logs(
    planned_arrival_date: str,
    _user: UserDep,
    limit: int = Query(default=200, ge=1, le=500),
):
    _ensure_tables()
    day = _parse_date(planned_arrival_date)
    rows = fetch_all(
        """
        SELECT id, planned_arrival_date, reservation_id, shipping_detail_id, action,
               operator_user_id, operator_name, operated_at, change_summary
        FROM dalian_packing_operation_logs
        WHERE planned_arrival_date=%s
        ORDER BY id DESC
        LIMIT %s
        """,
        (day, limit),
    )
    data = []
    for row in rows:
        item = {key: _jsonable(value) for key, value in row.items()}
        summary = item.get("change_summary") or ""
        if isinstance(summary, str) and summary:
            try:
                item["change_summary"] = json.loads(summary)
            except json.JSONDecodeError:
                item["change_summary"] = summary
        data.append(item)
    return {"status": "success", "rows": data}
