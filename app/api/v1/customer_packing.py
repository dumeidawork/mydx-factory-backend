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
from app.utils.drawing_no import drawing_no_base, drawing_no_match_variants, normalize_drawing_input
from app.api.v1.drawing_flange_type_maps import load_flange_type_map, lookup_flange_type
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
    factory_order_no VARCHAR(128) NOT NULL DEFAULT '' COMMENT '本厂单号',
    customer VARCHAR(128) NOT NULL DEFAULT '' COMMENT '客户',
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
    box_no INT NULL COMMENT '主箱号（件数最多的箱）',
    actual_delivery_date DATE NULL COMMENT '实际交货日期（与离港日分开）',
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

CREATE_ORDER_CONTRACT_SQL = """
CREATE TABLE IF NOT EXISTS france_customer_packing_order_contracts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    list_id BIGINT NOT NULL COMMENT '箱单主表 id',
    order_no VARCHAR(64) NOT NULL COMMENT 'PO / 订单号',
    contract_archive_id INT NOT NULL COMMENT '关联客户合同 id',
    contract_file_name VARCHAR(255) NOT NULL DEFAULT '' COMMENT '合同文件名快照',
    contract_version VARCHAR(32) NOT NULL DEFAULT '' COMMENT '合同版本号快照',
    contract_total_amount DECIMAL(14,2) NULL COMMENT '关联时的合同总金额',
    UNIQUE KEY uk_fcpl_oc_list_order (list_id, order_no),
    INDEX idx_fcpl_oc_list (list_id),
    INDEX idx_fcpl_oc_contract (contract_archive_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='法国箱单：订单号与合同一对一'
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
    (
        "factory_order_no",
        "ALTER TABLE france_customer_packing_lists ADD COLUMN factory_order_no VARCHAR(128) NOT NULL DEFAULT '' COMMENT '本厂单号' AFTER contract_total_amount",
    ),
    (
        "customer",
        "ALTER TABLE france_customer_packing_lists ADD COLUMN customer VARCHAR(128) NOT NULL DEFAULT '' COMMENT '客户' AFTER factory_order_no",
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
    (
        "box_no",
        "ALTER TABLE france_customer_packing_list_items ADD COLUMN box_no INT NULL COMMENT '主箱号（件数最多的箱）' AFTER match_warning",
    ),
    (
        "actual_delivery_date",
        "ALTER TABLE france_customer_packing_list_items ADD COLUMN actual_delivery_date DATE NULL COMMENT '实际交货日期（与离港日分开）' AFTER box_no",
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


class OrderContractIn(BaseModel):
    order_no: str
    contract_archive_id: int = 0
    defer_contract: bool = False


class SavePackingRequest(BaseModel):
    contract_archive_id: int = 0
    departure_date: str = ""
    arrival_date: str = ""
    source_file_name: str = ""
    factory_order_no: str = ""
    customer: str = ""
    amount_checked: bool = False
    packing_tax_sum: float | None = None
    contract_total_amount: float | None = None
    order_contracts: list[OrderContractIn] = Field(default_factory=list)
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
    execute(CREATE_ORDER_CONTRACT_SQL)
    if _TABLES_READY:
        return
    execute(CREATE_LIST_SQL)
    execute(CREATE_ITEM_SQL)
    execute(CREATE_LOG_SQL)
    execute(CREATE_ORDER_CONTRACT_SQL)
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


def _contract_hit(archive_id: int) -> dict[str, Any]:
    archive = fetch_one(
        """
        SELECT id, total_amount, customer_name, file_name, version, owner_user_id, owner_name
        FROM customer_contract_archives WHERE id=%s
        """,
        (int(archive_id),),
    )
    if not archive:
        return {
            "contract_archive_id": int(archive_id),
            "id": int(archive_id),
            "order_no": "",
            "file_name": "",
            "version": "",
            "customer_name": "",
            "owner_name": "",
            "total_amount": None,
            "contract_total_amount_live": None,
            "can_link": False,
        }
    return {
        "contract_archive_id": int(archive["id"]),
        "id": int(archive["id"]),
        "order_no": "",
        "file_name": str(archive.get("file_name") or ""),
        "version": str(archive.get("version") or ""),
        "contract_file_name": str(archive.get("file_name") or ""),
        "contract_version": str(archive.get("version") or ""),
        "customer_name": archive.get("customer_name") or "",
        "owner_name": archive.get("owner_name") or "",
        "owner_user_id": archive.get("owner_user_id"),
        "total_amount": _jsonable(archive.get("total_amount")),
        "contract_total_amount_live": _jsonable(archive.get("total_amount")),
        "can_link": True,
    }


def _load_order_contracts(list_id: int) -> list[dict]:
    rows = fetch_all(
        """
        SELECT * FROM france_customer_packing_order_contracts
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


def _legacy_order_contracts(header: dict, items: list[dict], order_key: str) -> list[dict]:
    archive_id = header.get("contract_archive_id")
    if not archive_id:
        return []
    order_nos = sorted(
        {str(it.get(order_key) or "").strip() for it in items if str(it.get(order_key) or "").strip()}
    )
    if not order_nos:
        return []
    hit = _contract_hit(int(archive_id))
    hit["contract_file_name"] = hit.get("file_name") or str(header.get("contract_file_name") or "")
    hit["contract_version"] = hit.get("version") or str(header.get("contract_version") or "")
    if hit.get("total_amount") is None and header.get("contract_total_amount") is not None:
        hit["total_amount"] = _jsonable(header.get("contract_total_amount"))
    return [{**hit, "order_no": order_no} for order_no in order_nos]


def _resolve_order_contracts(header: dict, items: list[dict], order_key: str) -> list[dict]:
    mapped = _load_order_contracts(int(header["id"]))
    if mapped:
        return mapped
    return _legacy_order_contracts(header, items, order_key)


def _replace_order_contracts(list_id: int, mappings: list[tuple[str, dict]]) -> None:
    execute("DELETE FROM france_customer_packing_order_contracts WHERE list_id=%s", (list_id,))
    for order_no, contract in mappings:
        total = contract.get("total_amount")
        execute(
            """
            INSERT INTO france_customer_packing_order_contracts
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


def _validate_order_contract_mappings(
    order_nos: list[str],
    req_mappings: list[OrderContractIn],
    user: CurrentUser,
) -> list[tuple[str, dict]]:
    if not order_nos:
        raise HTTPException(status_code=400, detail="没有有效订单号，无法关联合同")
    by_order: dict[str, int] = {}
    for item in req_mappings:
        key = str(item.order_no or "").strip()
        if key:
            by_order[key] = int(item.contract_archive_id)
    missing = [order_no for order_no in order_nos if order_no not in by_order]
    if missing:
        raise HTTPException(status_code=400, detail=f"以下订单号尚未关联合同：{'、'.join(missing)}")
    used: dict[int, str] = {}
    result: list[tuple[str, dict]] = []
    for order_no in order_nos:
        archive_id = by_order[order_no]
        prior = used.get(archive_id)
        if prior and prior != order_no:
            raise HTTPException(
                status_code=400,
                detail=f"合同 #{archive_id} 已关联订单号 {prior}，每个订单号须对应不同合同文件",
            )
        used[archive_id] = order_no
        result.append((order_no, _load_own_contract(archive_id, user)))
    return result


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
        "material_matched": 0,
        "price_matched": 0,
    }


SPLIT_PRICE_MISSING_WARNING = "价格拆分表无此图纸的有效报价，当前价格为 0"
MANUAL_PRICE_WARNING = "价格拆分表无有效报价，当前为手工填写"
_SPEC_KEYS = ("spec_model", "spec", "model", "material")


def _material_payload(row: dict) -> dict[str, Any]:
    return {
        "drawing_no": str(row.get("drawing_no") or ""),
        "spec_model": str(row.get("spec_model") or ""),
        "spec": str(row.get("spec") or ""),
        "model": str(row.get("model") or ""),
        "material": str(row.get("material") or ""),
        "product_unit_price": 0.0,
        "agreement_price": 0.0,
    }


def _fill_split_prices(result: dict[str, Any], material_no: str) -> bool:
    """图纸已选定时从法国价格拆分有效行取价。缺报价或价格为 0 时返回 True。"""
    from app.services.material_align import load_order_prices_from_split

    drawing = str(result.get("drawing_no") or "").strip()
    if not drawing:
        result["product_unit_price"] = 0.0
        result["agreement_price"] = 0.0
        result["price_matched"] = 0
        return False
    prices = load_order_prices_from_split(material_no, drawing)
    agreement = round(float(prices.get("france_agreement_price") or 0), 2)
    product = round(float(prices.get("france_tax_inclusive_price") or 0), 2)
    result["agreement_price"] = agreement
    result["product_unit_price"] = product
    missing = agreement == 0 or product == 0
    result["price_matched"] = 0 if missing else 1
    return missing


def _finish_enrich(result: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    result["match_warning"] = "；".join(w for w in warnings if w)
    return result


def _note_price_match(result: dict[str, Any], material_no: str, warnings: list[str]) -> None:
    if _fill_split_prices(result, material_no):
        if str(result.get("drawing_no") or "").strip():
            warnings.append(SPLIT_PRICE_MISSING_WARNING)


def _price_warning_for_values(warning: str, product_unit_price: Any, agreement_price: Any, price_matched: int) -> str:
    if price_matched:
        return warning
    if float(product_unit_price or 0) <= 0 or float(agreement_price or 0) <= 0:
        return warning
    if SPLIT_PRICE_MISSING_WARNING in warning:
        return warning.replace(SPLIT_PRICE_MISSING_WARNING, MANUAL_PRICE_WARNING)
    return warning


def _merge_enrich_into(merged: dict[str, Any], extra: dict[str, Any], *, drawing_changed: bool) -> None:
    """命中档案时用回填值覆盖；未命中时保留手工规格和价格。换图纸仍未命中则清空规格和价格。"""
    if int(extra.get("material_matched") or 0):
        for key in ("drawing_no", *_SPEC_KEYS, "drawing_candidates", "multi_drawing", "drawing_confirmed"):
            merged[key] = extra.get(key)
        merged["material_matched"] = 1
    else:
        if drawing_changed:
            for key in _SPEC_KEYS:
                merged[key] = ""
        merged["drawing_no"] = extra.get("drawing_no") or merged.get("drawing_no") or ""
        merged["drawing_candidates"] = extra.get("drawing_candidates") or []
        merged["multi_drawing"] = int(extra.get("multi_drawing") or 0)
        merged["drawing_confirmed"] = 1 if str(merged.get("drawing_no") or "").strip() else 0
        merged["material_matched"] = 0
    if int(extra.get("price_matched") or 0):
        merged["product_unit_price"] = extra.get("product_unit_price") or 0
        merged["agreement_price"] = extra.get("agreement_price") or 0
        merged["price_matched"] = 1
    else:
        merged["price_matched"] = 0
        if drawing_changed:
            merged["product_unit_price"] = 0
            merged["agreement_price"] = 0
    merged["match_warning"] = _price_warning_for_values(
        str(extra.get("match_warning") or ""),
        merged.get("product_unit_price"),
        merged.get("agreement_price"),
        int(merged.get("price_matched") or 0),
    )


def enrich_material(material_no: str, dn: str = "", drawing_no: str = "") -> dict[str, Any]:
    from app.api.v1.workflow import _ensure_materials_table

    key_no = (material_no or "").strip()
    wanted_drawing = normalize_drawing_input(drawing_no)
    result = _empty_enrich()
    if not key_no:
        result["match_warning"] = "物料号为空"
        return result
    _ensure_materials_table()
    rows = fetch_all(
        """
        SELECT material_no, drawing_no, spec_model, spec, model, material
        FROM materials
        WHERE material_no=%s
        ORDER BY id
        """,
        (key_no,),
    )
    if not rows:
        result["drawing_no"] = wanted_drawing
        result["drawing_confirmed"] = 1 if wanted_drawing else 0
        warnings = ["未找到物料，请手工填写图纸与规格"]
        _note_price_match(result, key_no, warnings)
        return _finish_enrich(result, warnings)

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
            candidates = [str(r.get("drawing_no") or "") for r in rows if r.get("drawing_no")]
            result["drawing_no"] = wanted_drawing
            result["drawing_candidates"] = candidates
            result["multi_drawing"] = 1 if len(set(candidates)) > 1 else 0
            result["drawing_confirmed"] = 1
            warnings = ["未找到所选图纸号，请手工填写规格"]
            _note_price_match(result, key_no, warnings)
            return _finish_enrich(result, warnings)
        pool = dn_matched or rows
        candidates = [str(r.get("drawing_no") or "") for r in pool if r.get("drawing_no")]
        result.update(_material_payload(picked))
        result["material_matched"] = 1
        result["drawing_candidates"] = candidates
        result["multi_drawing"] = 1 if len(set(candidates)) > 1 else 0
        result["drawing_confirmed"] = 1
        warnings = []
        _note_price_match(result, key_no, warnings)
        return _finish_enrich(result, warnings)

    if not dn_wanted:
        candidates = [str(r.get("drawing_no") or "") for r in rows if r.get("drawing_no")]
        result["drawing_candidates"] = candidates
        result["multi_drawing"] = 1 if len(set(candidates)) > 1 else 0
        result["match_warning"] = "规格为空，请选择或填写图纸号"
        return result

    if not dn_matched:
        candidates = [str(r.get("drawing_no") or "") for r in rows if r.get("drawing_no")]
        result["drawing_candidates"] = candidates
        result["multi_drawing"] = 1 if len(set(candidates)) > 1 else 0
        result["match_warning"] = "物料号已找到但规格不匹配，请选择或填写图纸号"
        return result

    ranked = sorted(dn_matched, key=lambda r: drawing_rev_number(str(r.get("drawing_no") or "")), reverse=True)
    picked = ranked[0]
    candidates = [str(r.get("drawing_no") or "") for r in ranked if r.get("drawing_no")]
    unique_drawings = list(dict.fromkeys(candidates))
    result.update(_material_payload(picked))
    result["material_matched"] = 1
    result["drawing_candidates"] = unique_drawings
    multi = len(unique_drawings) > 1
    result["multi_drawing"] = 1 if multi else 0
    result["drawing_confirmed"] = 0 if multi else 1
    warnings = []
    if multi:
        warnings.append("存在多个图纸版本，已选 Rev 最大者，请确认")
    _note_price_match(result, key_no, warnings)
    return _finish_enrich(result, warnings)


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
        normalize_drawing_input(data.get("drawing_no")),
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
    execute("DELETE FROM france_customer_packing_order_contracts WHERE list_id=%s", (list_id,))
    execute("DELETE FROM france_customer_packing_list_items WHERE list_id=%s", (list_id,))
    execute("DELETE FROM france_customer_packing_lists WHERE id=%s", (list_id,))


def _list_payload(row: dict, items: list[dict] | None = None) -> dict:
    payload = _row_to_dict(row)
    payload["items"] = items if items is not None else _load_items(int(row["id"]))
    payload["order_contracts"] = _resolve_order_contracts(row, payload["items"], "po")
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
    factory_order_no = str(req.factory_order_no or "").strip()
    customer = str(req.customer or "").strip()
    if not factory_order_no:
        raise HTTPException(status_code=400, detail="本厂单号不能为空")
    if not customer:
        raise HTTPException(status_code=400, detail="客户不能为空")
    if any(not str(item.po or "").strip() for item in req.items):
        raise HTTPException(status_code=400, detail="存在未填订单号的明细，无法按订单号关联合同")
    po_list = sorted({str(item.po or "").strip() for item in req.items if str(item.po or "").strip()})
    mappings = list(req.order_contracts)
    if not mappings and req.contract_archive_id:
        mappings = [OrderContractIn(order_no=po, contract_archive_id=req.contract_archive_id) for po in po_list]
    loaded_maps = _validate_order_contract_mappings(po_list, mappings, user)
    contract = loaded_maps[0][1]
    packing_tax_sum = _sum_tax_total(req.items)
    contract_total = _round2(sum(_round2(c.get("total_amount") or 0) for _, c in loaded_maps))
    checked_at = datetime.now()
    departure = _parse_date(req.departure_date)
    arrival = _parse_date(req.arrival_date)
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
                factory_order_no=%s, customer=%s,
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
                factory_order_no,
                customer,
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
                factory_order_no, customer,
                amount_checked, amount_checked_at, packing_tax_sum, contract_total_amount,
                created_by, created_by_user_id, updated_by, updated_by_user_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(contract["id"]),
                str(contract.get("file_name") or ""),
                str(contract.get("version") or ""),
                departure,
                arrival,
                req.source_file_name or "",
                factory_order_no,
                customer,
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
    _replace_order_contracts(list_id, loaded_maps)

    _write_log(
        user,
        action,
        list_id=list_id,
        change_summary={
            "contract_archive_id": int(contract["id"]),
            "order_contracts": [{"order_no": po, "contract_archive_id": int(c["id"])} for po, c in loaded_maps],
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
        clauses.append(
            """
            (l.contract_file_name LIKE %s OR EXISTS (
                SELECT 1 FROM france_customer_packing_order_contracts oc
                WHERE oc.list_id = l.id AND oc.contract_file_name LIKE %s
            ))
            """
        )
        like = f"%{contract_file_name.strip()}%"
        params.extend([like, like])
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
    previous_drawing = normalize_drawing_input(current.get("drawing_no"))
    merged = _row_to_dict(current)
    for key, value in (req.patch or {}).items():
        if key in allowed:
            merged[key] = value
    if any(k in req.patch for k in ("material_no", "dn", "drawing_no")):
        if "drawing_no" not in req.patch and any(k in req.patch for k in ("material_no", "dn")):
            merged["drawing_no"] = ""
        else:
            merged["drawing_no"] = normalize_drawing_input(merged.get("drawing_no"))
        drawing_changed = normalize_drawing_input(merged.get("drawing_no")) != previous_drawing
        extra = enrich_material(
            str(merged.get("material_no") or ""),
            str(merged.get("dn") or ""),
            str(merged.get("drawing_no") or ""),
        )
        _merge_enrich_into(merged, extra, drawing_changed=drawing_changed)
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
            normalize_drawing_input(merged.get("drawing_no")),
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
    mapped = _resolve_order_contracts(header, items, "po")
    by_order = {str(m.get("order_no") or "").strip(): m for m in mapped}
    factory_order_no = str(header.get("factory_order_no") or "").strip()
    customer = str(header.get("customer") or "").strip()
    if not factory_order_no:
        raise HTTPException(status_code=400, detail="本厂单号不能为空，请先保存整单")
    if not customer:
        raise HTTPException(status_code=400, detail="客户不能为空，请先保存整单")

    warnings = []
    flange_by_drawing = load_flange_type_map()
    for idx, item in enumerate(items, start=1):
        if not str(item.get("material_no") or "").strip():
            warnings.append(f"第{idx}行物料号为空")
        if not str(item.get("po") or "").strip():
            warnings.append(f"第{idx}行 PO 为空")
        if int(item.get("multi_drawing") or 0) and not int(item.get("drawing_confirmed") or 0):
            warnings.append(f"第{idx}行存在多个图纸版本未确认")
        drawing_raw = str(item.get("drawing_no") or "").strip()
        if not drawing_raw:
            warnings.append(f"第{idx}行未匹配图纸号")
        elif not lookup_flange_type(drawing_raw, flange_by_drawing):
            warnings.append(f"第{idx}行图纸号未匹配法兰类型（{drawing_no_base(drawing_raw)}）")
        if float(item.get("agreement_price") or 0) == 0 or float(item.get("product_unit_price") or 0) == 0:
            warnings.append(f"第{idx}行价格拆分表无有效报价")
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
    for po in grouped:
        hit = by_order.get(po)
        if not hit or not hit.get("contract_archive_id"):
            raise HTTPException(status_code=400, detail=f"订单号 {po} 未关联合同")
        owner_id = int(hit.get("owner_user_id") or 0)
        if owner_id != int(user.id):
            raise HTTPException(status_code=400, detail=f"订单号 {po} 的合同只能由该合同业务负责人生成订单明细")

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
            drawing_no = normalize_drawing_input(item.get("drawing_no"))
            material_no = str(item.get("material_no") or "")
            doc_status = _compute_doc_status(drawing_no, material_no)
            if doc_status == DOC_STATUS_PENDING:
                pending_count += 1
            quantity = int(item.get("quantity") or 0)
            unit_weight = round(float(item.get("unit_weight") or 0), 2)
            total_weight = calc_total_weight(unit_weight, quantity)
            agreement_price = round(float(item.get("agreement_price") or 0), 2)
            product_unit_price = round(float(item.get("product_unit_price") or 0), 2)
            remark1 = lookup_flange_type(drawing_no, flange_by_drawing)
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
                    factory_order_no,
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
                    remark1,
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
                "factory_order_no": factory_order_no,
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
                "remark1": remark1,
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
