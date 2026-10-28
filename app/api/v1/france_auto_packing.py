"""法国自动装箱：预览、行保存、提交、操作日志。"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.api.v1.customer_packing import _ensure_tables as _ensure_customer_packing
from app.core.database import execute, execute_many, fetch_all, fetch_one
from app.services.france_auto_packing import (
    annotate_posted_rows,
    existing_packing_count,
    factory_order_no_for,
    list_headers_for_order,
    main_box_no_by_item,
    pick_list_id,
    preview_order,
    renumber_rows,
)
from app.services.in_memory_store import now_iso
from app.services.material_drawing_link import backfill_material_drawing_archive_ids, ensure_material_drawing_archive_column

router = APIRouter(prefix="/france-auto-packing", tags=["法国自动装箱"])
UserDep = Annotated[CurrentUser, Depends(get_current_user)]

_TABLES_READY = False
_BACKFILL_DONE = False

CREATE_LOG_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS france_auto_packing_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_no VARCHAR(64) NOT NULL DEFAULT '',
    list_id BIGINT NULL,
    action VARCHAR(32) NOT NULL COMMENT 'preview/save/submit/overwrite/renumber/export/delete',
    operator_user_id INT NULL,
    operator_name VARCHAR(64) NOT NULL DEFAULT '',
    operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    box_count INT NOT NULL DEFAULT 0,
    row_count INT NOT NULL DEFAULT 0,
    overwrite TINYINT NOT NULL DEFAULT 0,
    change_summary TEXT NULL COMMENT '变更内容 JSON',
    INDEX idx_fr_auto_pack_log_order (order_no),
    INDEX idx_fr_auto_pack_log_list (list_id),
    INDEX idx_fr_auto_pack_log_action (action),
    INDEX idx_fr_auto_pack_log_time (operated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='法国自动装箱操作日志'
"""

PACKING_INSERT_SQL = """
    INSERT INTO packing_details (
        order_no, material_no, spec, standard, material, quantity, unit_weight, total_weight,
        gross_weight, remark1, box_no, entry_no, box_length, box_width, box_height,
        packing_remark, delivery_date, packed_at,
        created_by, created_by_user_id, updated_by, updated_by_user_id
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


class PackingRowIn(BaseModel):
    row_key: str = ""
    id: int | None = None
    item_id: int | None = None
    box_no: int = 0
    pos: int | None = None
    entry_no: int | None = None
    material_no: str = ""
    spec: str = ""
    standard: str = ""
    model: str = ""
    material: str = ""
    dn: str = ""
    flange_type: str = ""
    remark1: str = ""
    quantity: int = 0
    unit_weight: float = 0
    total_weight: float = 0
    gross_weight: float = 0
    qty_per_pallet: int = 0
    qty_source: str = ""
    pallet_name: str = ""
    pallet_type: str = ""
    packing_remark: str = ""
    box_length: int = 0
    box_width: int = 0
    box_height: int = 0
    box_weight: float = 20
    warnings: list[str] = Field(default_factory=list)
    blocking: list[str] = Field(default_factory=list)


class SaveRowRequest(BaseModel):
    order_no: str
    list_id: int | None = None
    box_no: int | None = None
    overwrite: bool = False
    renumber: bool = False
    rows: list[PackingRowIn] = Field(default_factory=list)


class SubmitRequest(BaseModel):
    order_no: str
    list_id: int | None = None
    overwrite: bool = False
    renumber: bool = False
    rows: list[PackingRowIn] = Field(default_factory=list)


class DeleteRowRequest(BaseModel):
    order_no: str
    list_id: int | None = None
    box_no: int | None = None
    packing_id: int | None = None
    row_key: str = ""


class MarkLogRequest(BaseModel):
    order_no: str
    list_id: int | None = None
    action: str = "export"
    box_count: int = 0
    row_count: int = 0
    change_summary: dict[str, Any] = Field(default_factory=dict)


def _ensure_tables() -> None:
    global _TABLES_READY, _BACKFILL_DONE
    _ensure_customer_packing()
    from app.api.v1.workflow import _ensure_materials_table, _ensure_packing_details_schema

    _ensure_materials_table()
    _ensure_packing_details_schema()
    ensure_material_drawing_archive_column()
    execute(CREATE_LOG_TABLE_SQL)
    if not _BACKFILL_DONE:
        backfill_material_drawing_archive_ids()
        _BACKFILL_DONE = True
    _TABLES_READY = True


def _write_log(
    user: CurrentUser,
    *,
    order_no: str,
    list_id: int | None,
    action: str,
    box_count: int = 0,
    row_count: int = 0,
    overwrite: bool = False,
    change_summary: dict[str, Any] | None = None,
) -> None:
    execute(
        """
        INSERT INTO france_auto_packing_logs
            (order_no, list_id, action, operator_user_id, operator_name, box_count, row_count, overwrite, change_summary)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            order_no,
            list_id,
            action,
            user.id,
            user.name,
            box_count,
            row_count,
            1 if overwrite else 0,
            json.dumps(change_summary or {}, ensure_ascii=False, default=str),
        ),
    )


def _rows_to_dicts(rows: list[PackingRowIn]) -> list[dict[str, Any]]:
    return [row.model_dump() for row in rows]


def _packing_snapshot(
    order_no: str,
    *,
    box_nos: list[int] | None = None,
    packing_id: int | None = None,
) -> list[dict[str, Any]]:
    fields = (
        "id, box_no, material_no, quantity, entry_no, box_length, box_width, box_height, packing_remark, remark1"
    )
    if packing_id:
        rows = fetch_all(
            f"SELECT {fields} FROM packing_details WHERE order_no=%s AND id=%s",
            (order_no, packing_id),
        )
    elif box_nos:
        numbers = [int(item) for item in box_nos if int(item)]
        if not numbers:
            return []
        placeholders = ", ".join(["%s"] * len(numbers))
        rows = fetch_all(
            f"SELECT {fields} FROM packing_details WHERE order_no=%s AND box_no IN ({placeholders}) ORDER BY box_no, id",
            (order_no, *numbers),
        )
    else:
        rows = fetch_all(
            f"SELECT {fields} FROM packing_details WHERE order_no=%s ORDER BY box_no, id",
            (order_no,),
        )
    keys = (
        "id",
        "box_no",
        "material_no",
        "quantity",
        "entry_no",
        "box_length",
        "box_width",
        "box_height",
        "packing_remark",
        "remark1",
    )
    return [{key: row.get(key) for key in keys} for row in rows]


def _posted_snapshot(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = (
        "box_no",
        "material_no",
        "quantity",
        "pos",
        "qty_per_pallet",
        "pallet_name",
        "box_length",
        "box_width",
        "box_height",
        "box_weight",
    )
    return [{key: row.get(key) for key in keys} for row in rows]


def _prepare_rows(req_rows: list[PackingRowIn], renumber: bool) -> list[dict[str, Any]]:
    rows = _rows_to_dicts(req_rows)
    if renumber:
        rows = renumber_rows(rows)
    return annotate_posted_rows(rows)


def _blocking_messages(rows: list[dict[str, Any]]) -> list[str]:
    msgs: list[str] = []
    for row in rows:
        for item in row.get("blocking") or []:
            if item not in msgs:
                msgs.append(str(item))
    return msgs


def _mark_order_packing(order_no: str, material_nos: list[str]) -> None:
    keys = sorted({m for m in material_nos if m})
    if not keys:
        return
    execute_many(
        """
        UPDATE order_details
        SET order_status=%s, updated_at=NOW()
        WHERE order_no=%s AND material_no=%s
        """,
        [("包装", order_no, material_no) for material_no in keys],
    )


def _write_customer_box_nos(rows: list[dict[str, Any]], splits: list[dict[str, Any]]) -> None:
    mapping = main_box_no_by_item(rows)
    for item_id, (main_box, all_boxes) in mapping.items():
        execute(
            "UPDATE france_customer_packing_list_items SET box_no=%s, updated_at=NOW() WHERE id=%s",
            (main_box, item_id),
        )
        if len(all_boxes) > 1:
            splits.append({"item_id": item_id, "main_box_no": main_box, "box_nos": all_boxes})


def _insert_packing_rows(
    order_no: str,
    rows: list[dict[str, Any]],
    user: CurrentUser,
    *,
    submitted: bool,
) -> None:
    now = now_iso() if submitted else None
    values: list[tuple[Any, ...]] = []
    first_in_box: set[int] = set()
    for row in rows:
        box_no = int(row.get("box_no") or 0)
        qty = int(row.get("quantity") or 0)
        unit_weight = round(float(row.get("unit_weight") or 0), 2)
        total_weight = round(qty * unit_weight, 2)
        box_weight = float(row.get("box_weight") or 0)
        is_first = box_no not in first_in_box
        if is_first:
            first_in_box.add(box_no)
        gross = round(total_weight + box_weight, 2) if is_first and submitted else (round(float(row.get("gross_weight") or 0), 2) if submitted else 0.0)
        values.append(
            (
                order_no,
                row.get("material_no") or "",
                row.get("spec") or "",
                row.get("standard") or row.get("model") or "",
                row.get("material") or "",
                qty,
                unit_weight,
                total_weight,
                gross,
                row.get("remark1") or row.get("flange_type") or "",
                box_no,
                int(row["pos"]) if row.get("pos") not in (None, "") else (int(row["entry_no"]) if row.get("entry_no") not in (None, "") else None),
                int(row.get("box_length") or 0),
                int(row.get("box_width") or 0),
                int(row.get("box_height") or 0),
                row.get("packing_remark") or row.get("pallet_type") or "",
                None,
                now,
                user.name,
                user.id,
                user.name,
                user.id,
            )
        )
    if values:
        execute_many(PACKING_INSERT_SQL, values)


def _delete_order_packing(order_no: str) -> None:
    execute("DELETE FROM packing_details WHERE order_no=%s", (order_no,))


def _delete_boxes(order_no: str, box_nos: list[int]) -> None:
    if not box_nos:
        return
    placeholders = ", ".join(["%s"] * len(box_nos))
    execute(
        f"DELETE FROM packing_details WHERE order_no=%s AND box_no IN ({placeholders})",
        (order_no, *box_nos),
    )


def _persist(
    user: CurrentUser,
    *,
    order_no: str,
    list_id: int | None,
    rows: list[dict[str, Any]],
    overwrite: bool,
    submitted: bool,
    box_nos: list[int] | None,
) -> dict[str, Any]:
    existing = existing_packing_count(order_no)
    if existing and not overwrite and not box_nos:
        raise HTTPException(status_code=409, detail="已有装箱明细，确认覆盖后再保存")
    inserted = rows if not box_nos else [r for r in rows if int(r.get("box_no") or 0) in set(box_nos)]
    if box_nos:
        _delete_boxes(order_no, box_nos)
    else:
        _delete_order_packing(order_no)
    _insert_packing_rows(order_no, inserted, user, submitted=submitted)
    _mark_order_packing(order_no, [str(r.get("material_no") or "") for r in inserted])
    splits: list[dict[str, Any]] = []
    _write_customer_box_nos(inserted, splits)
    from app.api.v1.workflow import _reload_packing_details_cache

    _reload_packing_details_cache()
    return {"splits": splits, "overwrite": bool(existing)}


@router.get("/preview")
def preview(user: UserDep, order_no: str, list_id: int | None = None):
    _ensure_tables()
    key = (order_no or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="请输入订单号")
    data = preview_order(key, list_id)
    if data.get("status") == "empty":
        raise HTTPException(status_code=404, detail=data.get("detail") or "未找到法国客户箱单")
    confirm = data.get("confirm") or {}
    _write_log(
        user,
        order_no=key,
        list_id=confirm.get("list_id"),
        action="preview",
        box_count=len({int(r.get("box_no") or 0) for r in data.get("rows") or []}),
        row_count=len(data.get("rows") or []),
        change_summary={"source_file_name": confirm.get("source_file_name"), "has_existing": data.get("has_existing_packing")},
    )
    return data


@router.post("/save-row")
def save_row(req: SaveRowRequest, user: UserDep):
    _ensure_tables()
    order_no = req.order_no.strip()
    if not order_no:
        raise HTTPException(status_code=400, detail="订单号不能为空")
    header = pick_list_id(order_no, req.list_id)
    list_id = int(header["id"]) if header else req.list_id
    rows = _prepare_rows(req.rows, req.renumber)
    if not rows:
        raise HTTPException(status_code=400, detail="没有可保存的行")
    target_box = int(req.box_no or 0)
    if not target_box:
        raise HTTPException(status_code=400, detail="请指定要保存的箱号")
    persist_rows = [r for r in rows if int(r.get("box_no") or 0) == target_box]
    if not persist_rows:
        persist_rows = rows
        target_box = int(persist_rows[0].get("box_no") or 0)
    before = _packing_snapshot(order_no, box_nos=[target_box])
    result = _persist(
        user,
        order_no=order_no,
        list_id=list_id,
        rows=rows,
        overwrite=True,
        submitted=False,
        box_nos=[target_box],
    )
    _write_log(
        user,
        order_no=order_no,
        list_id=list_id,
        action="save",
        box_count=1,
        row_count=len(persist_rows),
        overwrite=result["overwrite"],
        change_summary={
            "op": "save",
            "box_no": target_box,
            "renumber": req.renumber,
            "splits": result["splits"],
            "before": before,
            "after": _posted_snapshot(persist_rows),
        },
    )
    if req.renumber:
        _write_log(user, order_no=order_no, list_id=list_id, action="renumber", row_count=len(rows))
    return {"status": "success", "rows": rows, "saved_box_no": target_box, "splits": result["splits"]}


@router.post("/submit")
def submit(req: SubmitRequest, user: UserDep):
    _ensure_tables()
    order_no = req.order_no.strip()
    if not order_no:
        raise HTTPException(status_code=400, detail="订单号不能为空")
    header = pick_list_id(order_no, req.list_id)
    list_id = int(header["id"]) if header else req.list_id
    existing = existing_packing_count(order_no)
    if existing and not req.overwrite:
        raise HTTPException(status_code=409, detail="已有装箱明细，确认覆盖后再提交")
    rows = _prepare_rows(req.rows, req.renumber)
    if not rows:
        raise HTTPException(status_code=400, detail="没有可提交的行")
    blocked = _blocking_messages(rows)
    if blocked:
        raise HTTPException(status_code=400, detail="存在未处理的红色项，请手改后再提交：" + "；".join(blocked[:6]))
    before = _packing_snapshot(order_no)
    result = _persist(
        user,
        order_no=order_no,
        list_id=list_id,
        rows=rows,
        overwrite=True,
        submitted=True,
        box_nos=None,
    )
    action = "overwrite" if existing else "submit"
    _write_log(
        user,
        order_no=order_no,
        list_id=list_id,
        action=action,
        box_count=len({int(r.get("box_no") or 0) for r in rows}),
        row_count=len(rows),
        overwrite=bool(existing),
        change_summary={
            "op": action,
            "renumber": req.renumber,
            "splits": result["splits"],
            "before": before,
            "after": _posted_snapshot(rows),
        },
    )
    if req.renumber:
        _write_log(user, order_no=order_no, list_id=list_id, action="renumber", row_count=len(rows))
    if action == "overwrite":
        _write_log(
            user,
            order_no=order_no,
            list_id=list_id,
            action="submit",
            box_count=len({int(r.get("box_no") or 0) for r in rows}),
            row_count=len(rows),
            overwrite=True,
        )
    return {
        "status": "success",
        "rows": rows,
        "factory_order_no": factory_order_no_for(order_no, header),
        "splits": result["splits"],
    }


@router.post("/delete-row")
def delete_row(req: DeleteRowRequest, user: UserDep):
    _ensure_tables()
    order_no = req.order_no.strip()
    if req.packing_id:
        before = _packing_snapshot(order_no, packing_id=int(req.packing_id))
        execute("DELETE FROM packing_details WHERE id=%s AND order_no=%s", (int(req.packing_id), order_no))
    elif req.box_no:
        before = _packing_snapshot(order_no, box_nos=[int(req.box_no)])
        _delete_boxes(order_no, [int(req.box_no)])
    else:
        raise HTTPException(status_code=400, detail="请指定要删除的行或箱号")
    from app.api.v1.workflow import _reload_packing_details_cache

    _reload_packing_details_cache()
    _write_log(
        user,
        order_no=order_no,
        list_id=req.list_id,
        action="delete",
        box_count=1 if req.box_no else 0,
        change_summary={"op": "delete", "packing_id": req.packing_id, "box_no": req.box_no, "row_key": req.row_key, "before": before},
    )
    return {"status": "success"}


@router.get("/logs")
def list_logs(
    _user: UserDep,
    order_no: str = "",
    limit: int = Query(200, ge=1, le=1000),
):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    if order_no.strip():
        clauses.append("order_no=%s")
        params.append(order_no.strip())
    rows = fetch_all(
        f"""
        SELECT id, order_no, list_id, action, operator_user_id, operator_name, operated_at,
               box_count, row_count, overwrite, change_summary
        FROM france_auto_packing_logs
        WHERE {' AND '.join(clauses)}
        ORDER BY operated_at DESC, id DESC
        LIMIT %s
        """,
        (*params, limit),
    )
    return {"status": "success", "rows": rows}


@router.post("/log")
def mark_log(req: MarkLogRequest, user: UserDep):
    _ensure_tables()
    if req.action not in {"export", "preview", "delete", "copy"}:
        raise HTTPException(status_code=400, detail="不支持的日志动作")
    _write_log(
        user,
        order_no=req.order_no.strip(),
        list_id=req.list_id,
        action=req.action,
        box_count=req.box_count,
        row_count=req.row_count,
        change_summary=req.change_summary,
    )
    return {"status": "success"}


@router.get("/lists")
def list_order_lists(_user: UserDep, order_no: str):
    _ensure_tables()
    return {"status": "success", "rows": list_headers_for_order(order_no.strip())}
