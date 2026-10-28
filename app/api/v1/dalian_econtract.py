"""大连电子合同录入 API。原件只读取不归档。"""
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
from app.api.v1.customer_packing import (
    OrderContractIn,
    _contract_hit,
    _load_own_contract,
    _validate_order_contract_mappings,
)
from app.core.database import execute, fetch_all, fetch_one
from app.services.dalian_econtract_parser import parse_dalian_econtract_workbook
from app.services.france_sea_packing_parser import calc_total_weight, dn_key, drawing_rev_number
from app.services.in_memory_store import now_iso
from app.services.material_align import load_order_prices_from_split
from app.services.order_detail_audit import (
    AUDIT_CREATE_COLUMNS,
    create_audit_values,
    snapshot_keys as order_detail_snapshot,
    stamp_create,
    write_op_log as write_order_detail_op_log,
)
from app.utils.drawing_no import drawing_no_match_variants, normalize_drawing_input

router = APIRouter(prefix="/customer-packing", tags=["客户箱单/电子合同录入"])
UserDep = Annotated[CurrentUser, Depends(get_current_user)]

_TABLES_READY = False
ALLOWED_EXT = {".xlsx", ".xls"}
PRICE_TOLERANCE = 0.01

CREATE_LIST_SQL = """
CREATE TABLE IF NOT EXISTS dalian_econtract_lists (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    contract_archive_id INT NOT NULL COMMENT '关联客户合同 id',
    contract_file_name VARCHAR(255) NOT NULL DEFAULT '' COMMENT '关联合同文件名快照',
    contract_version VARCHAR(32) NOT NULL DEFAULT '' COMMENT '关联合同版本号快照',
    source_file_name VARCHAR(255) NOT NULL DEFAULT '' COMMENT '上传时的文件名（不存原件）',
    factory_order_no VARCHAR(128) NOT NULL DEFAULT '' COMMENT '本厂单号',
    customer VARCHAR(128) NOT NULL DEFAULT '' COMMENT '客户',
    price_checked TINYINT NOT NULL DEFAULT 0 COMMENT '核对单价是否已确认',
    price_checked_at DATETIME NULL COMMENT '核对单价确认时间',
    created_by VARCHAR(64) NULL COMMENT '上传者',
    created_by_user_id INT NULL COMMENT '上传者用户ID',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
    updated_by VARCHAR(64) NULL COMMENT '更新者',
    updated_by_user_id INT NULL COMMENT '更新者用户ID',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_decl_contract (contract_archive_id),
    INDEX idx_decl_created_by (created_by_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大连电子合同主表'
"""

CREATE_ITEM_SQL = """
CREATE TABLE IF NOT EXISTS dalian_econtract_items (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    list_id BIGINT NOT NULL COMMENT '主表 id',
    seq INT NOT NULL DEFAULT 0 COMMENT '序号',
    part_no VARCHAR(128) NOT NULL DEFAULT '' COMMENT '料号',
    material_no VARCHAR(128) NOT NULL DEFAULT '' COMMENT '物料号',
    dn VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'DN大小',
    order_no VARCHAR(64) NOT NULL DEFAULT '' COMMENT '订单号',
    excel_unit_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '电子合同单价',
    split_unit_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '价格拆分表净价',
    material_unit_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '物料大连协议价',
    price_mismatch TINYINT NOT NULL DEFAULT 0 COMMENT 'Excel单价与拆分/物料价不一致',
    quantity INT NOT NULL DEFAULT 0 COMMENT '数量',
    drawing_no VARCHAR(255) NOT NULL DEFAULT '' COMMENT '图纸号',
    spec_model VARCHAR(256) NOT NULL DEFAULT '' COMMENT '规格型号',
    spec VARCHAR(64) NOT NULL DEFAULT '' COMMENT '规格',
    model VARCHAR(64) NOT NULL DEFAULT '' COMMENT '型号',
    material VARCHAR(64) NOT NULL DEFAULT '' COMMENT '材质',
    unit_weight DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '单重',
    total_weight DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '总重',
    remark1 VARCHAR(255) NOT NULL DEFAULT '' COMMENT '备注',
    drawing_candidates JSON NULL COMMENT '可选图纸号列表',
    multi_drawing TINYINT NOT NULL DEFAULT 0 COMMENT '是否存在多个图纸版本',
    drawing_confirmed TINYINT NOT NULL DEFAULT 0 COMMENT '多版本是否已确认',
    match_warning VARCHAR(255) NOT NULL DEFAULT '' COMMENT '回填警告',
    created_by VARCHAR(64) NULL COMMENT '上传者',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
    updated_by VARCHAR(64) NULL COMMENT '更新者',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_deci_list (list_id),
    INDEX idx_deci_order (order_no),
    INDEX idx_deci_material (material_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大连电子合同明细'
"""

CREATE_LOG_SQL = """
CREATE TABLE IF NOT EXISTS dalian_econtract_operation_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    list_id BIGINT NULL,
    item_id BIGINT NULL,
    action VARCHAR(32) NOT NULL COMMENT 'parse/create/update/delete/overwrite/generate_order',
    operator_user_id INT NULL,
    operator_name VARCHAR(64) NOT NULL DEFAULT '',
    operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    change_summary TEXT NULL COMMENT '变更内容 JSON',
    INDEX idx_decl_log_list (list_id),
    INDEX idx_decl_log_item (item_id),
    INDEX idx_decl_log_action (action),
    INDEX idx_decl_log_time (operated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大连电子合同操作日志'
"""

CREATE_ORDER_CONTRACT_SQL = """
CREATE TABLE IF NOT EXISTS dalian_econtract_order_contracts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    list_id BIGINT NOT NULL COMMENT '电子合同主表 id',
    order_no VARCHAR(64) NOT NULL COMMENT '订单号',
    contract_archive_id INT NOT NULL COMMENT '关联客户合同 id',
    contract_file_name VARCHAR(255) NOT NULL DEFAULT '' COMMENT '合同文件名快照',
    contract_version VARCHAR(32) NOT NULL DEFAULT '' COMMENT '合同版本号快照',
    contract_total_amount DECIMAL(14,2) NULL COMMENT '关联时的合同总金额',
    UNIQUE KEY uk_decl_oc_list_order (list_id, order_no),
    INDEX idx_decl_oc_list (list_id),
    INDEX idx_decl_oc_contract (contract_archive_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大连电子合同：订单号与合同一对一'
"""

ITEM_COLUMNS = (
    "seq, part_no, material_no, dn, order_no, excel_unit_price, split_unit_price, material_unit_price, "
    "price_mismatch, quantity, drawing_no, spec_model, spec, model, material, unit_weight, total_weight, "
    "remark1, drawing_candidates, multi_drawing, drawing_confirmed, match_warning"
)


class EcontractItemIn(BaseModel):
    seq: int = 0
    part_no: str = ""
    material_no: str = ""
    dn: str = ""
    order_no: str = ""
    excel_unit_price: float = 0
    split_unit_price: float = 0
    material_unit_price: float = 0
    price_mismatch: int = 0
    quantity: int = 0
    drawing_no: str = ""
    spec_model: str = ""
    spec: str = ""
    model: str = ""
    material: str = ""
    unit_weight: float = 0
    total_weight: float = 0
    remark1: str = ""
    drawing_candidates: list[str] = Field(default_factory=list)
    multi_drawing: int = 0
    drawing_confirmed: int = 0
    match_warning: str = ""


class SaveEcontractRequest(BaseModel):
    contract_archive_id: int = 0
    source_file_name: str = ""
    factory_order_no: str = ""
    customer: str = ""
    price_checked: bool = False
    order_contracts: list[OrderContractIn] = Field(default_factory=list)
    items: list[EcontractItemIn]


class ItemPatchRequest(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)


class AppendItemRequest(BaseModel):
    row: EcontractItemIn


class EnrichRequest(BaseModel):
    part_no: str = ""
    material_no: str
    dn: str = ""
    drawing_no: str = ""
    quantity: int = 0
    excel_unit_price: float = 0
    unit_weight: float = 0


class ApplyDrawingRequest(BaseModel):
    drawing_no: str


class GenerateOrdersRequest(BaseModel):
    confirm_warnings: bool = False


def _ensure_tables() -> None:
    global _TABLES_READY
    execute(CREATE_ORDER_CONTRACT_SQL)
    if _TABLES_READY:
        return
    execute(CREATE_LIST_SQL)
    execute(CREATE_ITEM_SQL)
    execute(CREATE_LOG_SQL)
    execute(CREATE_ORDER_CONTRACT_SQL)
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
        INSERT INTO dalian_econtract_operation_logs
            (list_id, item_id, action, operator_user_id, operator_name, change_summary)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (list_id, item_id, action, user.id, user.name, summary),
    )


def _load_list(list_id: int) -> dict:
    row = fetch_one("SELECT * FROM dalian_econtract_lists WHERE id=%s", (list_id,))
    if not row:
        raise HTTPException(status_code=404, detail="电子合同不存在")
    return row


def _load_order_contracts(list_id: int) -> list[dict]:
    rows = fetch_all(
        """
        SELECT * FROM dalian_econtract_order_contracts
        WHERE list_id=%s ORDER BY order_no, id
        """,
        (list_id,),
    )
    out: list[dict] = []
    for row in rows:
        hit = _contract_hit(int(row["contract_archive_id"]))
        hit["order_no"] = str(row.get("order_no") or "").strip()
        hit["contract_file_name"] = str(row.get("contract_file_name") or hit.get("file_name") or "")
        hit["contract_version"] = str(row.get("contract_version") or hit.get("version") or "")
        if hit.get("total_amount") is None and row.get("contract_total_amount") is not None:
            hit["total_amount"] = _jsonable(row.get("contract_total_amount"))
        out.append(hit)
    return out


def _legacy_order_contracts(header: dict, items: list[dict]) -> list[dict]:
    archive_id = header.get("contract_archive_id")
    if not archive_id:
        return []
    order_nos = sorted(
        {str(it.get("order_no") or "").strip() for it in items if str(it.get("order_no") or "").strip()}
    )
    if not order_nos:
        return []
    hit = _contract_hit(int(archive_id))
    hit["contract_file_name"] = hit.get("file_name") or str(header.get("contract_file_name") or "")
    hit["contract_version"] = hit.get("version") or str(header.get("contract_version") or "")
    return [{**hit, "order_no": order_no} for order_no in order_nos]


def _resolve_order_contracts(header: dict, items: list[dict]) -> list[dict]:
    mapped = _load_order_contracts(int(header["id"]))
    if mapped:
        return mapped
    return _legacy_order_contracts(header, items)


def _replace_order_contracts(list_id: int, mappings: list[tuple[str, dict]]) -> None:
    execute("DELETE FROM dalian_econtract_order_contracts WHERE list_id=%s", (list_id,))
    for order_no, contract in mappings:
        total = contract.get("total_amount")
        execute(
            """
            INSERT INTO dalian_econtract_order_contracts
                (list_id, order_no, contract_archive_id, contract_file_name, contract_version, contract_total_amount)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                list_id,
                order_no,
                int(contract["id"]),
                str(contract.get("file_name") or ""),
                str(contract.get("version") or ""),
                None if total is None else _round2(total),
            ),
        )


def _load_items(list_id: int) -> list[dict]:
    rows = fetch_all(
        "SELECT * FROM dalian_econtract_items WHERE list_id=%s ORDER BY seq, id",
        (list_id,),
    )
    return [_row_to_dict(r) for r in rows]


def _round2(value: Any) -> float:
    return round(float(value or 0), 2)


def _price_differs(left: Any, right: Any) -> bool:
    return abs(_round2(left) - _round2(right)) > PRICE_TOLERANCE


def _clear_price_checked(list_id: int, user: CurrentUser) -> None:
    execute(
        """
        UPDATE dalian_econtract_lists SET
            price_checked=0, price_checked_at=NULL,
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
        "unit_weight": 0.0,
        "split_unit_price": 0.0,
        "material_unit_price": 0.0,
        "price_mismatch": 0,
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
        "unit_weight": _round2(row.get("unit_weight") or 0),
        "material_unit_price": _round2(row.get("dalian_agreement_price") or 0),
        "part_no_from_material": str(row.get("part_no") or "").strip(),
    }


def _dalian_split_net_price(material_no: str, drawing_no: str, part_no: str) -> float:
    prices = load_order_prices_from_split(material_no, drawing_no, part_no)
    split_price = _round2(prices.get("dalian_agreement_price") or 0)
    key_part = str(part_no or "").strip()
    if split_price or not key_part:
        return split_price
    table = fetch_one(
        """
        SELECT 1 AS n FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = 'price_split_dalian'
        LIMIT 1
        """
    )
    if not table:
        return 0.0
    row = fetch_one(
        """
        SELECT net_price FROM price_split_dalian
        WHERE is_valid=1 AND TRIM(COALESCE(part_no, ''))=%s
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (key_part,),
    )
    return _round2((row or {}).get("net_price") or 0)


def _apply_price_compare(result: dict[str, Any], excel_unit_price: float, part_no: str, material_no: str, drawing_no: str) -> dict[str, Any]:
    split_price = _dalian_split_net_price(material_no, drawing_no, part_no)
    result["split_unit_price"] = split_price
    material_price = _round2(result.get("material_unit_price") or 0)
    excel_price = _round2(excel_unit_price)
    mismatch = 0
    warnings = [w for w in str(result.get("match_warning") or "").split("；") if w]
    if excel_price and split_price and _price_differs(excel_price, split_price):
        mismatch = 1
        warnings.append(f"Excel单价 {excel_price:.2f} 与拆分表 {split_price:.2f} 不一致")
    if excel_price and material_price and _price_differs(excel_price, material_price):
        mismatch = 1
        warnings.append(f"Excel单价 {excel_price:.2f} 与物料协议价 {material_price:.2f} 不一致")
    if excel_price and not split_price and not material_price:
        warnings.append("未找到拆分表/物料大连协议价，请核对")
    result["price_mismatch"] = mismatch
    result["match_warning"] = "；".join(warnings)
    return result


def enrich_dalian_row(
    material_no: str,
    dn: str = "",
    drawing_no: str = "",
    part_no: str = "",
    excel_unit_price: float = 0,
    quantity: int = 0,
    unit_weight_fallback: float = 0,
) -> dict[str, Any]:
    from app.api.v1.workflow import _ensure_materials_table

    key_no = (material_no or "").strip()
    result = _empty_enrich()
    if unit_weight_fallback:
        result["unit_weight"] = _round2(unit_weight_fallback)
    if not key_no:
        result["match_warning"] = "物料号为空"
        result["total_weight"] = calc_total_weight(result["unit_weight"], quantity)
        return _apply_price_compare(result, excel_unit_price, part_no, key_no, drawing_no)

    _ensure_materials_table()
    rows = fetch_all(
        """
        SELECT material_no, part_no, drawing_no, spec_model, spec, model, material,
               unit_weight, dalian_agreement_price
        FROM materials
        WHERE material_no=%s
        ORDER BY id
        """,
        (key_no,),
    )
    if not rows:
        result["match_warning"] = "未找到物料"
        result["total_weight"] = calc_total_weight(result["unit_weight"], quantity)
        return _apply_price_compare(result, excel_unit_price, part_no, key_no, drawing_no)

    wanted_drawing = normalize_drawing_input(drawing_no)
    wanted_variants = set(drawing_no_match_variants(wanted_drawing)) if wanted_drawing else set()
    dn_wanted = dn_key(dn)

    def _row_matches_dn(row: dict) -> bool:
        if not dn_wanted:
            return False
        if dn_key(row.get("spec")) == dn_wanted:
            return True
        blob = f"{row.get('spec_model') or ''} {row.get('spec') or ''}".upper().replace(" ", "")
        return f"DN{dn_wanted}" in blob or f"NB{dn_wanted}" in blob

    dn_matched = [r for r in rows if _row_matches_dn(r)]

    def _finish(picked: dict, pool: list[dict], confirmed: int, extra_warnings: list[str]) -> dict[str, Any]:
        payload = _material_payload(picked)
        part_from_mat = payload.pop("part_no_from_material", "")
        result.update(payload)
        candidates = [str(r.get("drawing_no") or "") for r in pool if r.get("drawing_no")]
        unique = list(dict.fromkeys(candidates))
        result["drawing_candidates"] = unique
        result["multi_drawing"] = 1 if len(unique) > 1 else 0
        result["drawing_confirmed"] = confirmed
        warnings = extra_warnings[:]
        if part_no and part_from_mat and part_no.strip() != part_from_mat:
            warnings.append(f"料号与物料建档不一致（建档 {part_from_mat}）")
        result["match_warning"] = "；".join(warnings)
        result["total_weight"] = calc_total_weight(result["unit_weight"], quantity)
        return _apply_price_compare(result, excel_unit_price, part_no, key_no, result["drawing_no"])

    if wanted_drawing:
        picked = next(
            (
                r
                for r in rows
                if str(r.get("drawing_no") or "") == wanted_drawing
                or str(r.get("drawing_no") or "") in wanted_variants
            ),
            None,
        )
        if not picked:
            result["match_warning"] = "未找到所选图纸号"
            result["drawing_candidates"] = [str(r.get("drawing_no") or "") for r in rows if r.get("drawing_no")]
            result["total_weight"] = calc_total_weight(result["unit_weight"], quantity)
            return _apply_price_compare(result, excel_unit_price, part_no, key_no, wanted_drawing)
        return _finish(picked, dn_matched or rows, 1, [])

    if not dn_wanted:
        result["drawing_candidates"] = [str(r.get("drawing_no") or "") for r in rows if r.get("drawing_no")]
        result["multi_drawing"] = 1 if len(set(result["drawing_candidates"])) > 1 else 0
        result["match_warning"] = "规格为空，请选择图纸号"
        result["total_weight"] = calc_total_weight(result["unit_weight"], quantity)
        return _apply_price_compare(result, excel_unit_price, part_no, key_no, drawing_no)

    if not dn_matched:
        result["drawing_candidates"] = [str(r.get("drawing_no") or "") for r in rows if r.get("drawing_no")]
        result["multi_drawing"] = 1 if len(set(result["drawing_candidates"])) > 1 else 0
        result["match_warning"] = "物料号已找到但规格不匹配，请选择图纸号"
        result["total_weight"] = calc_total_weight(result["unit_weight"], quantity)
        return _apply_price_compare(result, excel_unit_price, part_no, key_no, drawing_no)

    ranked = sorted(dn_matched, key=lambda r: drawing_rev_number(str(r.get("drawing_no") or "")), reverse=True)
    picked = ranked[0]
    unique_drawings = list(dict.fromkeys([str(r.get("drawing_no") or "") for r in ranked if r.get("drawing_no")]))
    extra = []
    if len(unique_drawings) > 1:
        extra.append("存在多个图纸版本，已选 Rev 最大者，请确认")
    return _finish(picked, ranked, 0 if len(unique_drawings) > 1 else 1, extra)


def _apply_enrich_to_item(item: dict[str, Any]) -> dict[str, Any]:
    extra = enrich_dalian_row(
        str(item.get("material_no") or ""),
        str(item.get("dn") or ""),
        str(item.get("drawing_no") or ""),
        str(item.get("part_no") or ""),
        float(item.get("excel_unit_price") or 0),
        int(item.get("quantity") or 0),
        float(item.get("unit_weight") or 0),
    )
    item.update(extra)
    return item


def _item_values(item: EcontractItemIn | dict, user: CurrentUser) -> tuple[Any, ...]:
    data = item.model_dump() if isinstance(item, EcontractItemIn) else dict(item)
    quantity = int(data.get("quantity") or 0)
    unit_weight = _round2(data.get("unit_weight") or 0)
    total_weight = calc_total_weight(unit_weight, quantity)
    candidates = data.get("drawing_candidates") or []
    if not isinstance(candidates, str):
        candidates = json.dumps(candidates, ensure_ascii=False)
    return (
        int(data.get("seq") or 0),
        str(data.get("part_no") or "").strip(),
        str(data.get("material_no") or "").strip(),
        str(data.get("dn") or "").strip(),
        str(data.get("order_no") or "").strip(),
        _round2(data.get("excel_unit_price") or 0),
        _round2(data.get("split_unit_price") or 0),
        _round2(data.get("material_unit_price") or 0),
        int(data.get("price_mismatch") or 0),
        quantity,
        normalize_drawing_input(data.get("drawing_no")),
        str(data.get("spec_model") or "").strip(),
        str(data.get("spec") or "").strip(),
        str(data.get("model") or "").strip(),
        str(data.get("material") or "").strip(),
        unit_weight,
        total_weight,
        str(data.get("remark1") or "").strip(),
        candidates,
        int(data.get("multi_drawing") or 0),
        int(data.get("drawing_confirmed") or 0),
        str(data.get("match_warning") or ""),
        user.name,
        user.name,
    )


def _insert_item(list_id: int, item: EcontractItemIn | dict, user: CurrentUser) -> int:
    values = _item_values(item, user)
    return execute(
        f"""
        INSERT INTO dalian_econtract_items
            (list_id, {ITEM_COLUMNS}, created_by, updated_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (list_id, *values),
    )


def _find_overwrite_ids(order_nos: list[str]) -> list[int]:
    if not order_nos:
        return []
    placeholders = ",".join(["%s"] * len(order_nos))
    rows = fetch_all(
        f"""
        SELECT DISTINCT l.id
        FROM dalian_econtract_lists l
        JOIN dalian_econtract_items i ON i.list_id = l.id
        WHERE i.order_no IN ({placeholders})
        ORDER BY l.id
        """,
        tuple(order_nos),
    )
    return [int(r["id"]) for r in rows]


def _delete_list(list_id: int) -> None:
    execute("DELETE FROM dalian_econtract_order_contracts WHERE list_id=%s", (list_id,))
    execute("DELETE FROM dalian_econtract_items WHERE list_id=%s", (list_id,))
    execute("DELETE FROM dalian_econtract_lists WHERE id=%s", (list_id,))


def _list_payload(row: dict, items: list[dict] | None = None) -> dict:
    payload = _row_to_dict(row)
    payload["items"] = items if items is not None else _load_items(int(row["id"]))
    payload["order_contracts"] = _resolve_order_contracts(row, payload["items"])
    first = payload["order_contracts"][0] if payload["order_contracts"] else None
    if first:
        payload["customer_name"] = first.get("customer_name")
        payload["owner_name"] = first.get("owner_name")
        payload["contract_total_amount_live"] = first.get("contract_total_amount_live")
    elif payload.get("contract_archive_id"):
        hit = _contract_hit(int(payload["contract_archive_id"]))
        payload["customer_name"] = hit.get("customer_name")
        payload["owner_name"] = hit.get("owner_name")
        payload["contract_total_amount_live"] = hit.get("contract_total_amount_live")
    return payload


@router.get("/dalian/contracts")
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


@router.post("/dalian/parse")
async def parse_dalian_econtract(user: UserDep, file: UploadFile = File(...)):
    _ensure_tables()
    filename = Path(file.filename or "econtract.xlsx").name
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="仅支持 Excel（.xlsx / .xls）")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    try:
        parsed = parse_dalian_econtract_workbook(content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取失败：{exc}") from exc
    items = [_apply_enrich_to_item(item) for item in parsed["items"]]
    _write_log(user, "parse", change_summary={"file_name": filename, "count": len(items), "order_nos": parsed["order_nos"]})
    return {
        "status": "success",
        "source_file_name": filename,
        "order_nos": parsed["order_nos"],
        "items": items,
    }


@router.post("/dalian/enrich")
def enrich_dalian_econtract_row(req: EnrichRequest, user: UserDep):
    _ensure_tables()
    extra = enrich_dalian_row(
        req.material_no,
        req.dn,
        req.drawing_no,
        req.part_no,
        req.excel_unit_price,
        req.quantity,
        req.unit_weight,
    )
    return {"status": "success", **extra}


@router.post("/dalian/save")
def save_dalian_econtract(req: SaveEcontractRequest, user: UserDep):
    _ensure_tables()
    if not req.items:
        raise HTTPException(status_code=400, detail="明细不能为空")
    if not req.price_checked:
        raise HTTPException(status_code=400, detail="请先完成【核对单价】后再保存整单")
    factory_order_no = str(req.factory_order_no or "").strip()
    customer = str(req.customer or "").strip()
    if not factory_order_no:
        raise HTTPException(status_code=400, detail="本厂单号不能为空")
    if not customer:
        raise HTTPException(status_code=400, detail="客户不能为空")
    if any(not str(item.order_no or "").strip() for item in req.items):
        raise HTTPException(status_code=400, detail="存在未填订单号的明细，无法按订单号关联合同")
    order_nos = sorted({str(item.order_no or "").strip() for item in req.items if str(item.order_no or "").strip()})
    mappings = list(req.order_contracts)
    if not mappings and req.contract_archive_id:
        mappings = [OrderContractIn(order_no=no, contract_archive_id=req.contract_archive_id) for no in order_nos]
    loaded_maps = _validate_order_contract_mappings(order_nos, mappings, user)
    contract = loaded_maps[0][1]
    checked_at = datetime.now()
    overwrite_ids = _find_overwrite_ids(order_nos)
    keep_id = overwrite_ids[0] if overwrite_ids else None
    old_summaries = []
    for old_id in overwrite_ids:
        old_summaries.append({"id": old_id, "items": _load_items(old_id)})
        if old_id != keep_id:
            _delete_list(old_id)

    if keep_id:
        execute(
            """
            UPDATE dalian_econtract_lists SET
                contract_archive_id=%s, contract_file_name=%s, contract_version=%s,
                source_file_name=%s, factory_order_no=%s, customer=%s,
                price_checked=1, price_checked_at=%s,
                updated_by=%s, updated_by_user_id=%s
            WHERE id=%s
            """,
            (
                int(contract["id"]),
                str(contract.get("file_name") or ""),
                str(contract.get("version") or ""),
                req.source_file_name or "",
                factory_order_no,
                customer,
                checked_at,
                user.name,
                user.id,
                keep_id,
            ),
        )
        execute("DELETE FROM dalian_econtract_items WHERE list_id=%s", (keep_id,))
        list_id = keep_id
        action = "overwrite"
    else:
        list_id = execute(
            """
            INSERT INTO dalian_econtract_lists (
                contract_archive_id, contract_file_name, contract_version,
                source_file_name, factory_order_no, customer,
                price_checked, price_checked_at,
                created_by, created_by_user_id, updated_by, updated_by_user_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(contract["id"]),
                str(contract.get("file_name") or ""),
                str(contract.get("version") or ""),
                req.source_file_name or "",
                factory_order_no,
                customer,
                1,
                checked_at,
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
    _replace_order_contracts(list_id, loaded_maps)

    _write_log(
        user,
        action,
        list_id=list_id,
        change_summary={
            "contract_archive_id": int(contract["id"]),
            "order_contracts": [{"order_no": no, "contract_archive_id": int(c["id"])} for no, c in loaded_maps],
            "order_nos": order_nos,
            "count": len(req.items),
            "overwritten": old_summaries,
            "source_file_name": req.source_file_name,
            "price_checked": True,
        },
    )
    header = _load_list(list_id)
    return {"status": "success", "overwritten": bool(overwrite_ids), "list": _list_payload(header)}


@router.get("/dalian/lists")
def list_dalian_econtracts(
    user: UserDep,
    order_no: str = Query(""),
    contract_file_name: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    if order_no.strip():
        clauses.append(
            "EXISTS (SELECT 1 FROM dalian_econtract_items i WHERE i.list_id = l.id AND i.order_no LIKE %s)"
        )
        params.append(f"%{order_no.strip()}%")
    if contract_file_name.strip():
        clauses.append(
            """
            (l.contract_file_name LIKE %s OR EXISTS (
                SELECT 1 FROM dalian_econtract_order_contracts oc
                WHERE oc.list_id = l.id AND oc.contract_file_name LIKE %s
            ))
            """
        )
        like = f"%{contract_file_name.strip()}%"
        params.extend([like, like])
    where = " AND ".join(clauses)
    rows = fetch_all(
        f"""
        SELECT l.* FROM dalian_econtract_lists l
        WHERE {where}
        ORDER BY l.updated_at DESC, l.id DESC
        LIMIT %s
        """,
        tuple(params + [limit]),
    )
    return {"status": "success", "items": [_row_to_dict(r) for r in rows]}


@router.get("/dalian/lists/{list_id}")
def get_dalian_econtract(list_id: int, user: UserDep):
    _ensure_tables()
    header = _load_list(list_id)
    return {"status": "success", "list": _list_payload(header)}


@router.delete("/dalian/lists/{list_id}")
def delete_dalian_econtract(list_id: int, user: UserDep):
    _ensure_tables()
    header = _load_list(list_id)
    items = _load_items(list_id)
    _delete_list(list_id)
    _write_log(user, "delete", list_id=list_id, change_summary={"header": _row_to_dict(header), "count": len(items)})
    return {"status": "success", "deleted": list_id}


@router.post("/dalian/lists/{list_id}/items")
def append_dalian_item(list_id: int, req: AppendItemRequest, user: UserDep):
    _ensure_tables()
    _load_list(list_id)
    row = req.row
    if not row.seq:
        found = fetch_one("SELECT COALESCE(MAX(seq), 0) AS max_seq FROM dalian_econtract_items WHERE list_id=%s", (list_id,))
        row.seq = int((found or {}).get("max_seq") or 0) + 1
    item_id = _insert_item(list_id, row, user)
    _clear_price_checked(list_id, user)
    saved = fetch_one("SELECT * FROM dalian_econtract_items WHERE id=%s", (item_id,))
    _write_log(user, "update", list_id=list_id, item_id=item_id, change_summary={"op": "append"})
    return {"status": "success", "item": _row_to_dict(saved)}


@router.patch("/dalian/items/{item_id}")
def patch_dalian_item(item_id: int, req: ItemPatchRequest, user: UserDep):
    _ensure_tables()
    current = fetch_one("SELECT * FROM dalian_econtract_items WHERE id=%s", (item_id,))
    if not current:
        raise HTTPException(status_code=404, detail="明细不存在")
    merged = _row_to_dict(current)
    merged.update(req.patch or {})
    if any(key in (req.patch or {}) for key in ("material_no", "dn", "drawing_no", "part_no", "excel_unit_price", "quantity")):
        extra = enrich_dalian_row(
            str(merged.get("material_no") or ""),
            str(merged.get("dn") or ""),
            str(merged.get("drawing_no") or ""),
            str(merged.get("part_no") or ""),
            float(merged.get("excel_unit_price") or 0),
            int(merged.get("quantity") or 0),
            float(merged.get("unit_weight") or 0),
        )
        merged.update(extra)
    values = _item_values(merged, user)
    field_count = len(ITEM_COLUMNS.split(", "))
    execute(
        f"""
        UPDATE dalian_econtract_items SET
            {", ".join(f"{col.strip()}=%s" for col in ITEM_COLUMNS.split(","))},
            updated_by=%s
        WHERE id=%s
        """,
        (*values[:field_count], user.name, item_id),
    )
    _clear_price_checked(int(current["list_id"]), user)
    saved = fetch_one("SELECT * FROM dalian_econtract_items WHERE id=%s", (item_id,))
    _write_log(user, "update", list_id=int(current["list_id"]), item_id=item_id, change_summary={"patch": req.patch})
    return {"status": "success", "item": _row_to_dict(saved)}


@router.post("/dalian/items/{item_id}/apply-drawing")
def apply_dalian_drawing(item_id: int, req: ApplyDrawingRequest, user: UserDep):
    return patch_dalian_item(item_id, ItemPatchRequest(patch={"drawing_no": req.drawing_no, "drawing_confirmed": 1}), user)


@router.delete("/dalian/items/{item_id}")
def delete_dalian_item(item_id: int, user: UserDep):
    _ensure_tables()
    current = fetch_one("SELECT * FROM dalian_econtract_items WHERE id=%s", (item_id,))
    if not current:
        raise HTTPException(status_code=404, detail="明细不存在")
    execute("DELETE FROM dalian_econtract_items WHERE id=%s", (item_id,))
    _clear_price_checked(int(current["list_id"]), user)
    _write_log(user, "update", list_id=int(current["list_id"]), item_id=item_id, change_summary={"op": "delete_item"})
    return {"status": "success", "deleted": item_id}


@router.get("/dalian/lists/{list_id}/logs")
def list_dalian_logs(list_id: int, user: UserDep):
    _ensure_tables()
    _load_list(list_id)
    rows = fetch_all(
        """
        SELECT * FROM dalian_econtract_operation_logs
        WHERE list_id=%s
        ORDER BY operated_at DESC, id DESC
        LIMIT 200
        """,
        (list_id,),
    )
    return {"status": "success", "items": [_row_to_dict(r) for r in rows]}


@router.post("/dalian/lists/{list_id}/generate-orders")
def generate_dalian_orders(list_id: int, req: GenerateOrdersRequest, user: UserDep):
    from app.api.v1.workflow import DOC_STATUS_PENDING, _compute_doc_status, _ensure_order_details_loaded
    from app.services.in_memory_store import order_details

    _ensure_tables()
    header = _load_list(list_id)
    items = _load_items(list_id)
    if not items:
        raise HTTPException(status_code=400, detail="电子合同明细为空，无法生成订单")
    if int(header.get("price_checked") or 0) != 1:
        raise HTTPException(status_code=400, detail="请先完成【核对单价】后再生成订单明细")
    mapped = _resolve_order_contracts(header, items)
    by_order = {str(m.get("order_no") or "").strip(): m for m in mapped}
    factory_order_no = str(header.get("factory_order_no") or "").strip()
    customer = str(header.get("customer") or "").strip()
    if not factory_order_no:
        raise HTTPException(status_code=400, detail="本厂单号不能为空，请先保存整单")
    if not customer:
        raise HTTPException(status_code=400, detail="客户不能为空，请先保存整单")

    warnings = []
    for idx, item in enumerate(items, start=1):
        if not str(item.get("material_no") or "").strip():
            warnings.append(f"第{idx}行物料号为空")
        order_no = str(item.get("order_no") or "").strip()
        if not order_no:
            warnings.append(f"第{idx}行订单号为空")
        elif not order_no.startswith("4"):
            warnings.append(f"第{idx}行订单号【{order_no}】不是 4 开头（大连订单）")
        if int(item.get("multi_drawing") or 0) and not int(item.get("drawing_confirmed") or 0):
            warnings.append(f"第{idx}行存在多个图纸版本未确认")
        if not str(item.get("drawing_no") or "").strip():
            warnings.append(f"第{idx}行未匹配图纸号")
        if int(item.get("price_mismatch") or 0):
            warnings.append(f"第{idx}行单价与拆分表/物料协议价不一致")
        if float(item.get("excel_unit_price") or 0) == 0:
            warnings.append(f"第{idx}行 Excel 单价为 0")
    if warnings and not req.confirm_warnings:
        raise HTTPException(status_code=400, detail={"message": "生成前请确认以下问题", "warnings": warnings})

    grouped: dict[str, list[dict]] = {}
    for item in items:
        order_no = str(item.get("order_no") or "").strip()
        if not order_no:
            continue
        grouped.setdefault(order_no, []).append(item)
    if not grouped:
        raise HTTPException(status_code=400, detail="没有有效的订单号，无法生成订单")
    for order_no in grouped:
        hit = by_order.get(order_no)
        if not hit or not hit.get("contract_archive_id"):
            raise HTTPException(status_code=400, detail=f"订单号 {order_no} 未关联合同")
        owner_id = int(hit.get("owner_user_id") or 0)
        if owner_id != int(user.id):
            raise HTTPException(status_code=400, detail=f"订单号 {order_no} 的合同只能由该合同业务负责人生成订单明细")

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
    for order_no, order_items in grouped.items():
        for seq, item in enumerate(order_items, start=1):
            drawing_no = normalize_drawing_input(item.get("drawing_no"))
            material_no = str(item.get("material_no") or "")
            doc_status = _compute_doc_status(drawing_no, material_no)
            if doc_status == DOC_STATUS_PENDING:
                pending_count += 1
            quantity = int(item.get("quantity") or 0)
            unit_weight = _round2(item.get("unit_weight") or 0)
            total_weight = calc_total_weight(unit_weight, quantity)
            excel_price = _round2(item.get("excel_unit_price") or 0)
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
                    order_no,
                    customer,
                    factory_order_no,
                    seq,
                    None,
                    str(item.get("part_no") or ""),
                    drawing_no,
                    material_no,
                    str(item.get("spec_model") or ""),
                    str(item.get("spec") or ""),
                    str(item.get("model") or ""),
                    str(item.get("material") or ""),
                    quantity,
                    unit_weight,
                    total_weight,
                    excel_price,
                    excel_price,
                    str(item.get("remark1") or ""),
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
                "order_no": order_no,
                "customer": customer,
                "factory_order_no": factory_order_no,
                "seq": seq,
                "item_no": None,
                "name": str(item.get("part_no") or ""),
                "drawing_no": drawing_no,
                "material_no": material_no,
                "spec_model": str(item.get("spec_model") or ""),
                "spec": str(item.get("spec") or ""),
                "standard": str(item.get("model") or ""),
                "material": str(item.get("material") or ""),
                "quantity": quantity,
                "unit_weight": unit_weight,
                "total_weight": total_weight,
                "agreement_price": excel_price,
                "product_unit_price": excel_price,
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
                order_no=order_no,
                material_no=material_no,
                change_summary={"op": "generate_from_dalian_econtract", **order_detail_snapshot(record)},
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
