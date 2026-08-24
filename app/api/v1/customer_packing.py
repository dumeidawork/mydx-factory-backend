"""法国客户附带箱单录入 API。原件只读取不归档。"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.core.database import execute, fetch_all, fetch_one
from app.services.france_sea_packing_parser import calc_total_weight, dn_key, drawing_rev_number, parse_sea_workbook
from app.services.in_memory_store import now_iso
from app.services.order_detail_audit import (
    AUDIT_CREATE_COLUMNS,
    create_audit_values,
    snapshot_keys as order_detail_snapshot,
    stamp_create,
    write_op_log as write_order_detail_op_log,
)

router = APIRouter(prefix="/customer-packing", tags=["客户箱单/电子合同录入"])
UserDep = Annotated[CurrentUser, Depends(get_current_user)]

_TABLES_READY = False
ALLOWED_EXT = {".xlsx", ".xls"}

CREATE_LIST_SQL = """
CREATE TABLE IF NOT EXISTS france_customer_packing_lists (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    contract_archive_id INT NOT NULL COMMENT '关联客户合同 id',
    contract_file_name VARCHAR(255) NOT NULL DEFAULT '' COMMENT '关联合同文件名快照',
    contract_version VARCHAR(32) NOT NULL DEFAULT '' COMMENT '关联合同版本号快照',
    departure_date DATE NULL COMMENT '预定离港日 C1',
    arrival_date DATE NULL COMMENT '预定到港日 C2',
    source_file_name VARCHAR(255) NOT NULL DEFAULT '' COMMENT '上传时的文件名（不存原件）',
    amount_checked TINYINT NOT NULL DEFAULT 0 COMMENT '核对总单价是否已确认',
    amount_checked_at DATETIME NULL COMMENT '核对总单价确认时间',
    packing_tax_sum DECIMAL(14,2) NULL COMMENT '核对当时的箱单含税合计',
    contract_total_amount DECIMAL(14,2) NULL COMMENT '核对当时的合同总金额',
    created_by VARCHAR(64) NULL COMMENT '上传者',
    created_by_user_id INT NULL COMMENT '上传者用户ID',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
    updated_by VARCHAR(64) NULL COMMENT '更新者',
    updated_by_user_id INT NULL COMMENT '更新者用户ID',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_fcpl_contract (contract_archive_id),
    INDEX idx_fcpl_departure (departure_date),
    INDEX idx_fcpl_created_by (created_by_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='法国客户附带箱单主表'
"""

CREATE_ITEM_SQL = """
CREATE TABLE IF NOT EXISTS france_customer_packing_list_items (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    list_id BIGINT NOT NULL COMMENT '主表 id',
    seq INT NOT NULL DEFAULT 0 COMMENT '序号',
    material_no VARCHAR(128) NOT NULL DEFAULT '' COMMENT 'Item 物料号',
    dn VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'DN 规格',
    units_per_pallet DECIMAL(12,4) NOT NULL DEFAULT 0 COMMENT 'Unites/Pallets 数量/托',
    quantity INT NOT NULL DEFAULT 0 COMMENT 'Quantity 订单数量',
    nb_of_pallets DECIMAL(12,4) NOT NULL DEFAULT 0 COMMENT 'Nb of Pallets 托盘数量',
    unit_weight DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT 'Unit Weight 单重',
    total_weight DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT 'Total Weight 总重',
    po VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'PO 订单号',
    pos INT NULL COMMENT 'Pos 条目',
    drawing_no VARCHAR(255) NOT NULL DEFAULT '' COMMENT '图纸号',
    spec_model VARCHAR(256) NOT NULL DEFAULT '' COMMENT '规格型号',
    spec VARCHAR(64) NOT NULL DEFAULT '' COMMENT '规格',
    model VARCHAR(64) NOT NULL DEFAULT '' COMMENT '型号',
    material VARCHAR(64) NOT NULL DEFAULT '' COMMENT '材质',
    product_unit_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '产品单价',
    agreement_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '法国协议价',
    tax_unit_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '含税单价（保留2位）',
    tax_total_amount DECIMAL(14,2) NOT NULL DEFAULT 0.00 COMMENT '含税总价（含税单价×数量）',
    drawing_candidates JSON NULL COMMENT '可选图纸号列表',
    multi_drawing TINYINT NOT NULL DEFAULT 0 COMMENT '是否存在多个图纸版本',
    drawing_confirmed TINYINT NOT NULL DEFAULT 0 COMMENT '多版本是否已确认',
    excel_total_weight DECIMAL(12,2) NULL COMMENT 'Excel G列原值（仅核对）',
    weight_mismatch TINYINT NOT NULL DEFAULT 0 COMMENT 'Excel总重与单重*数量不一致',
    match_warning VARCHAR(255) NOT NULL DEFAULT '' COMMENT '回填警告',
    created_by VARCHAR(64) NULL COMMENT '上传者',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
    updated_by VARCHAR(64) NULL COMMENT '更新者',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_fcpli_list (list_id),
    INDEX idx_fcpli_po_pos (po, pos),
    INDEX idx_fcpli_material (material_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='法国客户附带箱单明细'
"""

CREATE_LOG_SQL = """
CREATE TABLE IF NOT EXISTS france_customer_packing_operation_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    list_id BIGINT NULL,
    item_id BIGINT NULL,
    action VARCHAR(32) NOT NULL COMMENT 'upload/parse/link_contract/create/update/delete/overwrite/generate_order',
    operator_user_id INT NULL,
    operator_name VARCHAR(64) NOT NULL DEFAULT '',
    operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    change_summary TEXT NULL COMMENT '变更内容 JSON',
    INDEX idx_fcpl_log_list (list_id),
    INDEX idx_fcpl_log_item (item_id),
    INDEX idx_fcpl_log_action (action),
    INDEX idx_fcpl_log_time (operated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='法国客户附带箱单操作日志'
"""

ITEM_COLUMNS = (
    "seq, material_no, dn, units_per_pallet, quantity, nb_of_pallets, unit_weight, total_weight, "
    "po, pos, drawing_no, spec_model, spec, model, material, product_unit_price, agreement_price, "
    "tax_unit_price, tax_total_amount, "
    "drawing_candidates, multi_drawing, drawing_confirmed, excel_total_weight, weight_mismatch, match_warning"
)

LIST_COLUMN_ALTERS = (
    (
        "amount_checked",
        "ALTER TABLE france_customer_packing_lists ADD COLUMN amount_checked TINYINT NOT NULL DEFAULT 0 COMMENT '核对总单价是否已确认' AFTER source_file_name",
    ),
    (
        "amount_checked_at",
        "ALTER TABLE france_customer_packing_lists ADD COLUMN amount_checked_at DATETIME NULL COMMENT '核对总单价确认时间' AFTER amount_checked",
    ),
    (
        "packing_tax_sum",
        "ALTER TABLE france_customer_packing_lists ADD COLUMN packing_tax_sum DECIMAL(14,2) NULL COMMENT '核对当时的箱单含税合计' AFTER amount_checked_at",
    ),
    (
        "contract_total_amount",
        "ALTER TABLE france_customer_packing_lists ADD COLUMN contract_total_amount DECIMAL(14,2) NULL COMMENT '核对当时的合同总金额' AFTER packing_tax_sum",
    ),
)

ITEM_COLUMN_ALTERS = (
    (
        "tax_unit_price",
        "ALTER TABLE france_customer_packing_list_items ADD COLUMN tax_unit_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '含税单价（保留2位）' AFTER agreement_price",
    ),
    (
        "tax_total_amount",
        "ALTER TABLE france_customer_packing_list_items ADD COLUMN tax_total_amount DECIMAL(14,2) NOT NULL DEFAULT 0.00 COMMENT '含税总价（含税单价×数量）' AFTER tax_unit_price",
    ),
)


class PackingItemIn(BaseModel):
    seq: int = 0
    material_no: str = ""
    dn: str = ""
    units_per_pallet: float = 0
    quantity: int = 0
    nb_of_pallets: float = 0
    unit_weight: float = 0
    total_weight: float = 0
    po: str = ""
    pos: int | None = None
    drawing_no: str = ""
    spec_model: str = ""
    spec: str = ""
    model: str = ""
    material: str = ""
    product_unit_price: float = 0
    agreement_price: float = 0
    tax_unit_price: float = 0
    tax_total_amount: float = 0
    drawing_candidates: list[str] = Field(default_factory=list)
    multi_drawing: int = 0
    drawing_confirmed: int = 0
    excel_total_weight: float | None = None
    weight_mismatch: int = 0
    match_warning: str = ""


class SavePackingRequest(BaseModel):
    contract_archive_id: int
    departure_date: str = ""
    arrival_date: str = ""
    source_file_name: str = ""
    amount_checked: bool = False
    packing_tax_sum: float | None = None
    contract_total_amount: float | None = None
    items: list[PackingItemIn]


class ItemPatchRequest(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)


class AppendItemRequest(BaseModel):
    row: PackingItemIn


class EnrichRequest(BaseModel):
    material_no: str
    dn: str = ""
    drawing_no: str = ""
    quantity: int = 0
    unit_weight: float = 0


class ApplyDrawingRequest(BaseModel):
    drawing_no: str


class GenerateOrdersRequest(BaseModel):
    confirm_warnings: bool = False


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


def _ensure_tables() -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    execute(CREATE_LIST_SQL)
    execute(CREATE_ITEM_SQL)
    execute(CREATE_LOG_SQL)
    for name, stmt in LIST_COLUMN_ALTERS:
        if not _column_exists("france_customer_packing_lists", name):
            execute(stmt)
    for name, stmt in ITEM_COLUMN_ALTERS:
        if not _column_exists("france_customer_packing_list_items", name):
            execute(stmt)
    _TABLES_READY = True


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _row_to_dict(row: dict | None) -> dict:
    if not row:
        return {}
    out = {k: _jsonable(v) for k, v in row.items()}
    raw = out.get("drawing_candidates")
    if isinstance(raw, str):
        try:
            out["drawing_candidates"] = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            out["drawing_candidates"] = []
    elif raw is None:
        out["drawing_candidates"] = []
    return out


def _write_log(
    user: CurrentUser,
    action: str,
    list_id: int | None = None,
    item_id: int | None = None,
    change_summary: dict | str | None = None,
) -> None:
    summary = change_summary
    if isinstance(summary, dict):
        summary = json.dumps(summary, ensure_ascii=False, default=str)
    execute(
        """
        INSERT INTO france_customer_packing_operation_logs
            (list_id, item_id, action, operator_user_id, operator_name, change_summary)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (list_id, item_id, action, user.id, user.name, summary),
    )


def _parse_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    compact = text.replace("/", "").replace("-", "")
    if len(compact) == 8 and compact.isdigit():
        return date(int(compact[:4]), int(compact[4:6]), int(compact[6:8]))
    return date.fromisoformat(text[:10])


def _load_own_contract(archive_id: int, user: CurrentUser) -> dict:
    row = fetch_one("SELECT * FROM customer_contract_archives WHERE id=%s", (archive_id,))
    if not row:
        raise HTTPException(status_code=404, detail="合同归档不存在")
    owner_id = int(row.get("owner_user_id") or 0)
    if owner_id != int(user.id):
        raise HTTPException(status_code=400, detail="只能关联业务负责人为当前登录者的合同")
    return row


def _load_list(list_id: int) -> dict:
    row = fetch_one("SELECT * FROM france_customer_packing_lists WHERE id=%s", (list_id,))
    if not row:
        raise HTTPException(status_code=404, detail="箱单不存在")
    return row


def _load_items(list_id: int) -> list[dict]:
    rows = fetch_all(
        "SELECT * FROM france_customer_packing_list_items WHERE list_id=%s ORDER BY seq, id",
        (list_id,),
    )
    return [_row_to_dict(r) for r in rows]


def _round2(value: Any) -> float:
    return round(float(value or 0), 2)


def _tax_unit_price(product_unit_price: Any) -> float:
    return _round2(product_unit_price)


def _tax_total_amount(tax_unit_price: Any, quantity: Any) -> float:
    return _round2(float(tax_unit_price or 0) * int(quantity or 0))


def _apply_tax_fields(item: dict[str, Any]) -> dict[str, Any]:
    tax = _tax_unit_price(item.get("product_unit_price"))
    item["tax_unit_price"] = tax
    item["tax_total_amount"] = _tax_total_amount(tax, item.get("quantity"))
    return item


def _sum_tax_total(items: list[Any]) -> float:
    total = 0.0
    for item in items:
        data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        _apply_tax_fields(data)
        total += float(data.get("tax_total_amount") or 0)
    return _round2(total)


def _clear_amount_checked(list_id: int, user: CurrentUser) -> None:
    execute(
        """
        UPDATE france_customer_packing_lists SET
            amount_checked=0, amount_checked_at=NULL,
            updated_by=%s, updated_by_user_id=%s
        WHERE id=%s
        """,
        (user.name, user.id, list_id),
    )


def _empty_enrich() -> dict[str, Any]:
    return {
        "drawing_no": "",
        "spec_model": "",
        "spec": "",
        "model": "",
        "material": "",
        "product_unit_price": 0.0,
        "agreement_price": 0.0,
        "drawing_candidates": [],
        "multi_drawing": 0,
        "drawing_confirmed": 0,
        "match_warning": "",
    }


def _material_payload(row: dict) -> dict[str, Any]:
    return {
        "drawing_no": str(row.get("drawing_no") or ""),
        "spec_model": str(row.get("spec_model") or ""),
        "spec": str(row.get("spec") or ""),
        "model": str(row.get("model") or ""),
        "material": str(row.get("material") or ""),
        "product_unit_price": float(row.get("product_unit_price") or 0),
        "agreement_price": float(row.get("france_agreement_price") or 0),
    }


def enrich_material(material_no: str, dn: str = "", drawing_no: str = "") -> dict[str, Any]:
    from app.api.v1.workflow import _ensure_materials_table

    key_no = (material_no or "").strip()
    result = _empty_enrich()
    if not key_no:
        result["match_warning"] = "物料号为空"
        return result
    _ensure_materials_table()
    rows = fetch_all(
        """
        SELECT material_no, drawing_no, spec_model, spec, model, material,
               product_unit_price, france_agreement_price
        FROM materials
        WHERE material_no=%s
        ORDER BY id
        """,
        (key_no,),
    )
    if not rows:
        result["match_warning"] = "未找到物料"
        return result

    wanted_drawing = (drawing_no or "").strip()
    dn_wanted = dn_key(dn)

    def _row_matches_dn(row: dict) -> bool:
        if not dn_wanted:
            return False
        if dn_key(row.get("spec")) == dn_wanted:
            return True
        blob = f"{row.get('spec_model') or ''} {row.get('spec') or ''}".upper().replace(" ", "")
        return f"DN{dn_wanted}" in blob or f"NB{dn_wanted}" in blob

    dn_matched = [r for r in rows if _row_matches_dn(r)]

    if wanted_drawing:
        picked = next((r for r in rows if str(r.get("drawing_no") or "") == wanted_drawing), None)
        if not picked:
            result["match_warning"] = "未找到所选图纸号"
            result["drawing_candidates"] = [str(r.get("drawing_no") or "") for r in rows if r.get("drawing_no")]
            return result
        pool = dn_matched or rows
        candidates = [str(r.get("drawing_no") or "") for r in pool if r.get("drawing_no")]
        result.update(_material_payload(picked))
        result["drawing_candidates"] = candidates
        result["multi_drawing"] = 1 if len(set(candidates)) > 1 else 0
        result["drawing_confirmed"] = 1
        if float(result["agreement_price"] or 0) == 0 or float(result["product_unit_price"] or 0) == 0:
            result["match_warning"] = "价格为0，请核对物料建档"
        return result

    if not dn_wanted:
        candidates = [str(r.get("drawing_no") or "") for r in rows if r.get("drawing_no")]
        result["drawing_candidates"] = candidates
        result["multi_drawing"] = 1 if len(set(candidates)) > 1 else 0
        result["match_warning"] = "规格为空，请选择图纸号"
        return result

    if not dn_matched:
        candidates = [str(r.get("drawing_no") or "") for r in rows if r.get("drawing_no")]
        result["drawing_candidates"] = candidates
        result["multi_drawing"] = 1 if len(set(candidates)) > 1 else 0
        result["match_warning"] = "物料号已找到但规格不匹配，请选择图纸号"
        return result

    ranked = sorted(dn_matched, key=lambda r: drawing_rev_number(str(r.get("drawing_no") or "")), reverse=True)
    picked = ranked[0]
    candidates = [str(r.get("drawing_no") or "") for r in ranked if r.get("drawing_no")]
    unique_drawings = list(dict.fromkeys(candidates))
    result.update(_material_payload(picked))
    result["drawing_candidates"] = unique_drawings
    multi = len(unique_drawings) > 1
    result["multi_drawing"] = 1 if multi else 0
    result["drawing_confirmed"] = 0 if multi else 1
    warnings = []
    if multi:
        warnings.append("存在多个图纸版本，已选 Rev 最大者，请确认")
    if float(result["agreement_price"] or 0) == 0 or float(result["product_unit_price"] or 0) == 0:
        warnings.append("价格为0，请核对物料建档")
    result["match_warning"] = "；".join(warnings)
    return result


def _apply_enrich_to_item(item: dict[str, Any]) -> dict[str, Any]:
    extra = enrich_material(
        str(item.get("material_no") or ""),
        str(item.get("dn") or ""),
        str(item.get("drawing_no") or ""),
    )
    item.update(extra)
    quantity = int(item.get("quantity") or 0)
    unit_weight = float(item.get("unit_weight") or 0)
    item["total_weight"] = calc_total_weight(unit_weight, quantity)
    _apply_tax_fields(item)
    return item


def _item_values(item: PackingItemIn | dict, user: CurrentUser) -> tuple[Any, ...]:
    data = item.model_dump() if isinstance(item, PackingItemIn) else dict(item)
    quantity = int(data.get("quantity") or 0)
    unit_weight = float(data.get("unit_weight") or 0)
    total_weight = calc_total_weight(unit_weight, quantity)
    product_unit_price = round(float(data.get("product_unit_price") or 0), 2)
    tax_unit_price = _tax_unit_price(product_unit_price)
    tax_total_amount = _tax_total_amount(tax_unit_price, quantity)
    candidates = data.get("drawing_candidates") or []
    if not isinstance(candidates, str):
        candidates = json.dumps(candidates, ensure_ascii=False)
    excel_total = data.get("excel_total_weight")
    return (
        int(data.get("seq") or 0),
        str(data.get("material_no") or "").strip(),
        str(data.get("dn") or "").strip(),
        float(data.get("units_per_pallet") or 0),
        quantity,
        float(data.get("nb_of_pallets") or 0),
        round(unit_weight, 2),
        total_weight,
        str(data.get("po") or "").strip(),
        data.get("pos"),
        str(data.get("drawing_no") or "").strip(),
        str(data.get("spec_model") or "").strip(),
        str(data.get("spec") or "").strip(),
        str(data.get("model") or "").strip(),
        str(data.get("material") or "").strip(),
        product_unit_price,
        round(float(data.get("agreement_price") or 0), 2),
        tax_unit_price,
        tax_total_amount,
        candidates,
        int(data.get("multi_drawing") or 0),
        int(data.get("drawing_confirmed") or 0),
        None if excel_total is None else round(float(excel_total), 2),
        int(data.get("weight_mismatch") or 0),
        str(data.get("match_warning") or ""),
        user.name,
        user.name,
    )


def _insert_item(list_id: int, item: PackingItemIn | dict, user: CurrentUser) -> int:
    values = _item_values(item, user)
    return execute(
        f"""
        INSERT INTO france_customer_packing_list_items
            (list_id, {ITEM_COLUMNS}, created_by, updated_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (list_id, *values),
    )


def _find_overwrite_ids(departure: date | None, po_list: list[str]) -> list[int]:
    if not departure or not po_list:
        return []
    placeholders = ",".join(["%s"] * len(po_list))
    rows = fetch_all(
        f"""
        SELECT DISTINCT l.id
        FROM france_customer_packing_lists l
        JOIN france_customer_packing_list_items i ON i.list_id = l.id
        WHERE l.departure_date=%s AND i.po IN ({placeholders})
        ORDER BY l.id
        """,
        tuple([departure, *po_list]),
    )
    return [int(r["id"]) for r in rows]


def _delete_list(list_id: int) -> None:
    execute("DELETE FROM france_customer_packing_list_items WHERE list_id=%s", (list_id,))
    execute("DELETE FROM france_customer_packing_lists WHERE id=%s", (list_id,))


def _list_payload(row: dict, items: list[dict] | None = None) -> dict:
    payload = _row_to_dict(row)
    payload["items"] = items if items is not None else _load_items(int(row["id"]))
    archive_id = payload.get("contract_archive_id")
    if archive_id:
        archive = fetch_one(
            "SELECT total_amount, customer_name, file_name, version, owner_user_id, owner_name FROM customer_contract_archives WHERE id=%s",
            (int(archive_id),),
        )
        if archive:
            payload["contract_total_amount_live"] = _jsonable(archive.get("total_amount"))
            payload["customer_name"] = archive.get("customer_name")
            payload["owner_name"] = archive.get("owner_name")
    return payload


@router.get("/france/contracts")
def list_linkable_contracts(
    user: UserDep,
    customer_name: str = Query(""),
    file_name: str = Query(""),
    version: str = Query(""),
    owner_name: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
):
    from app.api.v1.contract_archives import _visibility_sql

    _ensure_tables()
    vis_sql, vis_params = _visibility_sql(user)
    clauses = [vis_sql]
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
    if owner_name.strip():
        clauses.append("owner_name LIKE %s")
        params.append(f"%{owner_name.strip()}%")
    where = " AND ".join(clauses)
    rows = fetch_all(
        f"""
        SELECT id, customer_name, file_name, version, owner_user_id, owner_name,
               progress_status, upload_date, total_amount
        FROM customer_contract_archives
        WHERE {where}
        ORDER BY updated_at DESC, id DESC
        LIMIT %s
        """,
        tuple(params + [limit]),
    )
    items = []
    for row in rows:
        item = _row_to_dict(row)
        item["can_link"] = int(row.get("owner_user_id") or 0) == int(user.id)
        items.append(item)
    return {"status": "success", "items": items}


@router.post("/france/parse")
async def parse_france_packing(user: UserDep, file: UploadFile = File(...)):
    _ensure_tables()
    filename = Path(file.filename or "packing.xlsx").name
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="仅支持 Excel（.xlsx / .xls）")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    try:
        parsed = parse_sea_workbook(content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取失败：{exc}") from exc
    items = [_apply_enrich_to_item(item) for item in parsed["items"]]
    _write_log(user, "parse", change_summary={"file_name": filename, "count": len(items), "po_list": parsed["po_list"]})
    return {
        "status": "success",
        "source_file_name": filename,
        "departure_date": parsed["departure_date"],
        "arrival_date": parsed["arrival_date"],
        "po_list": parsed["po_list"],
        "items": items,
    }


@router.post("/france/enrich")
def enrich_france_row(req: EnrichRequest, user: UserDep):
    _ensure_tables()
    extra = enrich_material(req.material_no, req.dn, req.drawing_no)
    quantity = int(req.quantity or 0)
    unit_weight = float(req.unit_weight or 0)
    extra["total_weight"] = calc_total_weight(unit_weight, quantity)
    extra["tax_unit_price"] = _tax_unit_price(extra.get("product_unit_price"))
    extra["tax_total_amount"] = _tax_total_amount(extra["tax_unit_price"], quantity)
    return {"status": "success", **extra}


@router.post("/france/save")
def save_france_packing(req: SavePackingRequest, user: UserDep):
    _ensure_tables()
    if not req.items:
        raise HTTPException(status_code=400, detail="明细不能为空")
    if not req.amount_checked:
        raise HTTPException(status_code=400, detail="请先完成【核对总单价】后再保存整单")
    contract = _load_own_contract(req.contract_archive_id, user)
    packing_tax_sum = _sum_tax_total(req.items)
    contract_total = None if contract.get("total_amount") is None else _round2(contract.get("total_amount"))
    checked_at = datetime.now()
    departure = _parse_date(req.departure_date)
    arrival = _parse_date(req.arrival_date)
    po_list = sorted({str(item.po or "").strip() for item in req.items if str(item.po or "").strip()})
    overwrite_ids = _find_overwrite_ids(departure, po_list)
    keep_id = overwrite_ids[0] if overwrite_ids else None
    old_summaries = []
    for old_id in overwrite_ids:
        old_summaries.append({"id": old_id, "items": _load_items(old_id)})
        if old_id != keep_id:
            _delete_list(old_id)

    if keep_id:
        execute(
            """
            UPDATE france_customer_packing_lists SET
                contract_archive_id=%s, contract_file_name=%s, contract_version=%s,
                departure_date=%s, arrival_date=%s, source_file_name=%s,
                amount_checked=1, amount_checked_at=%s, packing_tax_sum=%s, contract_total_amount=%s,
                updated_by=%s, updated_by_user_id=%s
            WHERE id=%s
            """,
            (
                int(contract["id"]),
                str(contract.get("file_name") or ""),
                str(contract.get("version") or ""),
                departure,
                arrival,
                req.source_file_name or "",
                checked_at,
                packing_tax_sum,
                contract_total,
                user.name,
                user.id,
                keep_id,
            ),
        )
        execute("DELETE FROM france_customer_packing_list_items WHERE list_id=%s", (keep_id,))
        list_id = keep_id
        action = "overwrite"
    else:
        list_id = execute(
            """
            INSERT INTO france_customer_packing_lists (
                contract_archive_id, contract_file_name, contract_version,
                departure_date, arrival_date, source_file_name,
                amount_checked, amount_checked_at, packing_tax_sum, contract_total_amount,
                created_by, created_by_user_id, updated_by, updated_by_user_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(contract["id"]),
                str(contract.get("file_name") or ""),
                str(contract.get("version") or ""),
                departure,
                arrival,
                req.source_file_name or "",
                1,
                checked_at,
                packing_tax_sum,
                contract_total,
                user.name,
                user.id,
                user.name,
                user.id,
            ),
        )
        action = "create"

    for idx, item in enumerate(req.items, start=1):
        if not item.seq:
            item.seq = idx
        _insert_item(list_id, item, user)

    _write_log(
        user,
        action,
        list_id=list_id,
        change_summary={
            "contract_archive_id": int(contract["id"]),
            "po_list": po_list,
            "count": len(req.items),
            "overwritten": old_summaries,
            "source_file_name": req.source_file_name,
            "amount_checked": True,
            "packing_tax_sum": packing_tax_sum,
            "contract_total_amount": contract_total,
        },
    )
    header = _load_list(list_id)
    return {"status": "success", "overwritten": bool(overwrite_ids), "list": _list_payload(header)}


@router.get("/france/lists")
def list_france_packing(
    user: UserDep,
    po: str = Query(""),
    contract_file_name: str = Query(""),
    departure_date: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    if po.strip():
        clauses.append(
            "EXISTS (SELECT 1 FROM france_customer_packing_list_items i WHERE i.list_id = l.id AND i.po LIKE %s)"
        )
        params.append(f"%{po.strip()}%")
    if contract_file_name.strip():
        clauses.append("l.contract_file_name LIKE %s")
        params.append(f"%{contract_file_name.strip()}%")
    if departure_date.strip():
        parsed = _parse_date(departure_date)
        if parsed:
            clauses.append("l.departure_date=%s")
            params.append(parsed)
    where = " AND ".join(clauses)
    rows = fetch_all(
        f"""
        SELECT l.* FROM france_customer_packing_lists l
        WHERE {where}
        ORDER BY l.updated_at DESC, l.id DESC
        LIMIT %s
        """,
        tuple(params + [limit]),
    )
    return {"status": "success", "items": [_row_to_dict(r) for r in rows]}


@router.get("/france/lists/{list_id}")
def get_france_packing(list_id: int, user: UserDep):
    _ensure_tables()
    header = _load_list(list_id)
    return {"status": "success", "list": _list_payload(header)}


@router.delete("/france/lists/{list_id}")
def delete_france_packing(list_id: int, user: UserDep):
    _ensure_tables()
    header = _load_list(list_id)
    items = _load_items(list_id)
    _delete_list(list_id)
    _write_log(user, "delete", list_id=list_id, change_summary={"header": _row_to_dict(header), "count": len(items)})
    return {"status": "success", "deleted": list_id}


@router.post("/france/lists/{list_id}/items")
def append_france_item(list_id: int, req: AppendItemRequest, user: UserDep):
    _ensure_tables()
    _load_list(list_id)
    row = req.row.model_dump()
    if not row.get("seq"):
        last = fetch_one(
            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM france_customer_packing_list_items WHERE list_id=%s",
            (list_id,),
        )
        row["seq"] = int((last or {}).get("max_seq") or 0) + 1
    _apply_enrich_to_item(row)
    item_id = _insert_item(list_id, row, user)
    _clear_amount_checked(list_id, user)
    _write_log(user, "create", list_id=list_id, item_id=item_id, change_summary=row)
    saved = fetch_one("SELECT * FROM france_customer_packing_list_items WHERE id=%s", (item_id,))
    return {"status": "success", "item": _row_to_dict(saved)}


@router.patch("/france/items/{item_id}")
def patch_france_item(item_id: int, req: ItemPatchRequest, user: UserDep):
    _ensure_tables()
    current = fetch_one("SELECT * FROM france_customer_packing_list_items WHERE id=%s", (item_id,))
    if not current:
        raise HTTPException(status_code=404, detail="明细不存在")
    allowed = {
        "seq",
        "material_no",
        "dn",
        "units_per_pallet",
        "quantity",
        "nb_of_pallets",
        "unit_weight",
        "po",
        "pos",
        "drawing_no",
        "spec_model",
        "spec",
        "model",
        "material",
        "product_unit_price",
        "agreement_price",
        "multi_drawing",
        "drawing_confirmed",
        "match_warning",
    }
    merged = _row_to_dict(current)
    for key, value in (req.patch or {}).items():
        if key in allowed:
            merged[key] = value
    if any(k in req.patch for k in ("material_no", "dn")) and "drawing_no" not in req.patch:
        merged["drawing_no"] = ""
        extra = enrich_material(str(merged.get("material_no") or ""), str(merged.get("dn") or ""), "")
        merged.update(extra)
    elif "drawing_no" in req.patch:
        extra = enrich_material(
            str(merged.get("material_no") or ""),
            str(merged.get("dn") or ""),
            str(merged.get("drawing_no") or ""),
        )
        merged.update(extra)
        merged["drawing_confirmed"] = 1
    merged["total_weight"] = calc_total_weight(float(merged.get("unit_weight") or 0), int(merged.get("quantity") or 0))
    _apply_tax_fields(merged)
    execute(
        """
        UPDATE france_customer_packing_list_items SET
            seq=%s, material_no=%s, dn=%s, units_per_pallet=%s, quantity=%s, nb_of_pallets=%s,
            unit_weight=%s, total_weight=%s, po=%s, pos=%s, drawing_no=%s, spec_model=%s, spec=%s,
            model=%s, material=%s, product_unit_price=%s, agreement_price=%s,
            tax_unit_price=%s, tax_total_amount=%s, drawing_candidates=%s,
            multi_drawing=%s, drawing_confirmed=%s, match_warning=%s, updated_by=%s
        WHERE id=%s
        """,
        (
            int(merged.get("seq") or 0),
            str(merged.get("material_no") or ""),
            str(merged.get("dn") or ""),
            float(merged.get("units_per_pallet") or 0),
            int(merged.get("quantity") or 0),
            float(merged.get("nb_of_pallets") or 0),
            round(float(merged.get("unit_weight") or 0), 2),
            float(merged.get("total_weight") or 0),
            str(merged.get("po") or ""),
            merged.get("pos"),
            str(merged.get("drawing_no") or ""),
            str(merged.get("spec_model") or ""),
            str(merged.get("spec") or ""),
            str(merged.get("model") or ""),
            str(merged.get("material") or ""),
            round(float(merged.get("product_unit_price") or 0), 2),
            round(float(merged.get("agreement_price") or 0), 2),
            float(merged.get("tax_unit_price") or 0),
            float(merged.get("tax_total_amount") or 0),
            json.dumps(merged.get("drawing_candidates") or [], ensure_ascii=False),
            int(merged.get("multi_drawing") or 0),
            int(merged.get("drawing_confirmed") or 0),
            str(merged.get("match_warning") or ""),
            user.name,
            item_id,
        ),
    )
    _clear_amount_checked(int(current["list_id"]), user)
    _write_log(user, "update", list_id=int(current["list_id"]), item_id=item_id, change_summary=req.patch)
    saved = fetch_one("SELECT * FROM france_customer_packing_list_items WHERE id=%s", (item_id,))
    return {"status": "success", "item": _row_to_dict(saved)}


@router.post("/france/items/{item_id}/apply-drawing")
def apply_france_drawing(item_id: int, req: ApplyDrawingRequest, user: UserDep):
    return patch_france_item(item_id, ItemPatchRequest(patch={"drawing_no": req.drawing_no}), user)


@router.delete("/france/items/{item_id}")
def delete_france_item(item_id: int, user: UserDep):
    _ensure_tables()
    current = fetch_one("SELECT * FROM france_customer_packing_list_items WHERE id=%s", (item_id,))
    if not current:
        raise HTTPException(status_code=404, detail="明细不存在")
    execute("DELETE FROM france_customer_packing_list_items WHERE id=%s", (item_id,))
    _clear_amount_checked(int(current["list_id"]), user)
    _write_log(user, "delete", list_id=int(current["list_id"]), item_id=item_id, change_summary=_row_to_dict(current))
    return {"status": "success", "deleted": item_id}


@router.get("/france/lists/{list_id}/logs")
def list_france_logs(list_id: int, user: UserDep):
    _ensure_tables()
    _load_list(list_id)
    rows = fetch_all(
        """
        SELECT * FROM france_customer_packing_operation_logs
        WHERE list_id=%s
        ORDER BY operated_at DESC, id DESC
        LIMIT 200
        """,
        (list_id,),
    )
    return {"status": "success", "items": [_row_to_dict(r) for r in rows]}


@router.post("/france/lists/{list_id}/generate-orders")
def generate_france_orders(list_id: int, req: GenerateOrdersRequest, user: UserDep):
    from app.api.v1.workflow import DOC_STATUS_PENDING, _compute_doc_status, _ensure_order_details_loaded
    from app.services.in_memory_store import order_details

    _ensure_tables()
    header = _load_list(list_id)
    items = _load_items(list_id)
    if not items:
        raise HTTPException(status_code=400, detail="箱单明细为空，无法生成订单")
    if int(header.get("amount_checked") or 0) != 1:
        raise HTTPException(status_code=400, detail="请先完成【核对总单价】后再生成订单明细")
    contract = fetch_one(
        "SELECT * FROM customer_contract_archives WHERE id=%s",
        (int(header["contract_archive_id"]),),
    )
    if not contract:
        raise HTTPException(status_code=400, detail="关联合同不存在")
    owner_id = int(contract.get("owner_user_id") or 0)
    if owner_id != int(user.id):
        raise HTTPException(status_code=400, detail="只能由该合同业务负责人生成订单明细")
    customer = str(contract.get("customer_name") or "").strip()
    if not customer:
        raise HTTPException(status_code=400, detail="关联合同缺少客户名")

    warnings = []
    for idx, item in enumerate(items, start=1):
        if not str(item.get("material_no") or "").strip():
            warnings.append(f"第{idx}行物料号为空")
        if not str(item.get("po") or "").strip():
            warnings.append(f"第{idx}行 PO 为空")
        if int(item.get("multi_drawing") or 0) and not int(item.get("drawing_confirmed") or 0):
            warnings.append(f"第{idx}行存在多个图纸版本未确认")
        if not str(item.get("drawing_no") or "").strip():
            warnings.append(f"第{idx}行未匹配图纸号")
        if float(item.get("agreement_price") or 0) == 0 or float(item.get("product_unit_price") or 0) == 0:
            warnings.append(f"第{idx}行价格为0")
    if warnings and not req.confirm_warnings:
        raise HTTPException(status_code=400, detail={"message": "生成前请确认以下问题", "warnings": warnings})

    grouped: dict[str, list[dict]] = {}
    for item in items:
        po = str(item.get("po") or "").strip()
        if not po:
            continue
        grouped.setdefault(po, []).append(item)
    if not grouped:
        raise HTTPException(status_code=400, detail="没有有效的 PO，无法生成订单")

    _ensure_order_details_loaded()
    existing_pos = {
        str(r.get("order_no") or "").strip()
        for r in order_details
        if str(r.get("order_no") or "").strip() in grouped
    }
    if not existing_pos:
        rows = fetch_all(
            f"SELECT DISTINCT order_no FROM order_details WHERE order_no IN ({','.join(['%s'] * len(grouped))})",
            tuple(grouped.keys()),
        )
        existing_pos = {str(r.get("order_no") or "").strip() for r in rows}
    if existing_pos:
        raise HTTPException(
            status_code=400,
            detail=f"订单号已存在，请到【订单查询】处理：{'、'.join(sorted(existing_pos))}",
        )

    now = now_iso()
    created_all: list[dict] = []
    pending_count = 0
    for po, po_items in grouped.items():
        for seq, item in enumerate(po_items, start=1):
            drawing_no = str(item.get("drawing_no") or "")
            material_no = str(item.get("material_no") or "")
            doc_status = _compute_doc_status(drawing_no, material_no)
            if doc_status == DOC_STATUS_PENDING:
                pending_count += 1
            quantity = int(item.get("quantity") or 0)
            unit_weight = round(float(item.get("unit_weight") or 0), 2)
            total_weight = calc_total_weight(unit_weight, quantity)
            agreement_price = round(float(item.get("agreement_price") or 0), 2)
            product_unit_price = round(float(item.get("product_unit_price") or 0), 2)
            record_id = execute(
                f"""
                INSERT INTO order_details (
                    order_no, customer, factory_order_no, seq, item_no, name, drawing_no, material_no,
                    spec_model, spec, standard, material, quantity, unit_weight, total_weight,
                    agreement_price, product_unit_price, remark1, remark2, heat_no, heat_treatment_batch_no,
                    order_status, doc_status, upload_type, material_mode, started_at, finished_at, {AUDIT_CREATE_COLUMNS}
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    po,
                    customer,
                    "",
                    seq,
                    item.get("pos"),
                    "",
                    drawing_no,
                    material_no,
                    str(item.get("spec_model") or ""),
                    str(item.get("spec") or ""),
                    str(item.get("model") or ""),
                    str(item.get("material") or ""),
                    quantity,
                    unit_weight,
                    total_weight,
                    agreement_price,
                    product_unit_price,
                    "",
                    "",
                    "",
                    "",
                    "开始",
                    doc_status,
                    "文件上传",
                    "",
                    now,
                    None,
                    *create_audit_values(user),
                ),
            )
            record = {
                "id": record_id,
                "order_no": po,
                "customer": customer,
                "factory_order_no": "",
                "seq": seq,
                "item_no": item.get("pos"),
                "name": "",
                "drawing_no": drawing_no,
                "material_no": material_no,
                "spec_model": str(item.get("spec_model") or ""),
                "spec": str(item.get("spec") or ""),
                "standard": str(item.get("model") or ""),
                "material": str(item.get("material") or ""),
                "quantity": quantity,
                "unit_weight": unit_weight,
                "total_weight": total_weight,
                "agreement_price": agreement_price,
                "product_unit_price": product_unit_price,
                "status": "开始",
                "doc_status": doc_status,
                "upload_type": "文件上传",
                "started_at": now,
            }
            stamp_create(record, user)
            order_details.append(record)
            created_all.append(record)
            write_order_detail_op_log(
                action="create",
                user=user,
                row_id=record_id,
                order_no=po,
                material_no=material_no,
                change_summary={"op": "generate_from_customer_packing", **order_detail_snapshot(record)},
            )

    _write_log(
        user,
        "generate_order",
        list_id=list_id,
        change_summary={"order_nos": list(grouped.keys()), "count": len(created_all), "warnings": warnings},
    )
    return {
        "status": "success",
        "count": len(created_all),
        "order_nos": list(grouped.keys()),
        "pending_docs_count": pending_count,
        "warnings": warnings,
    }
