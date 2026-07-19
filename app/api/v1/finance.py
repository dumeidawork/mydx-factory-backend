"""财务管理 API：付款审批和多语言对账。"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.api.deps import get_accept_language
from app.core.database import execute, execute_many, fetch_all, fetch_one
from app.core.i18n import get_message
from app.services.in_memory_store import now_iso

router = APIRouter(prefix="/finance", tags=["财务"])

APPROVER_ROLES = {"general_manager", "super_admin"}


class CreatePaymentRequest(BaseModel):
    category: str
    amount: float
    applicant: str
    remark: str = ""


class ApprovePaymentRequest(BaseModel):
    payment_id: int
    approved: bool
    remark: str | None = None


class ReconcileRequest(BaseModel):
    contract_no: str
    client_name: str
    amount: float
    currency: str = "CNY"
    invoice_no: str = ""


@router.post("/payment")
def create_payment(req: CreatePaymentRequest):
    payment_id = execute(
        """
        INSERT INTO financial_records (type, category, amount, status, voucher_url)
        VALUES (%s, %s, %s, %s, %s)
        """,
        ("expense", req.category, req.amount, "pending_approval", req.remark),
    )
    payment = {
        "id": payment_id,
        "category": req.category,
        "amount": req.amount,
        "applicant": req.applicant,
        "remark": req.remark,
        "status": "pending_approval",
        "created_at": now_iso(),
    }
    return {"status": "success", "payment": payment}


@router.post("/approve_payment")
def approve_payment(
    req: ApprovePaymentRequest,
    accept_language: str = Depends(get_accept_language),
    x_role: str = Header(default="sales"),
):
    payment = fetch_one(
        "SELECT id, category, amount, voucher_url AS remark, status, created_at FROM financial_records WHERE id=%s",
        (req.payment_id,),
    )
    if not payment:
        raise HTTPException(status_code=404, detail=get_message(accept_language, "not_found"))

    if x_role not in APPROVER_ROLES:
        msg = get_message(accept_language, "auth_failed")
        return {"status": "error", "message": msg}

    payment["status"] = "approved" if req.approved else "rejected"
    payment["approved_by_role"] = x_role
    payment["approved_at"] = now_iso()
    payment["approval_remark"] = req.remark or ""
    execute(
        "UPDATE financial_records SET status=%s, voucher_url=%s, updated_at=NOW() WHERE id=%s",
        (payment["status"], payment["approval_remark"], req.payment_id),
    )
    return {"status": "success", "message": "审批成功", "payment": payment}


@router.post("/reconcile")
def create_reconciliation(req: ReconcileRequest):
    rec_id = execute(
        """
        INSERT INTO reconciliations (contract_no, client_name, amount, currency, invoice_no, status)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (req.contract_no, req.client_name, req.amount, req.currency, req.invoice_no, "generated"),
    )
    rec = {
        "id": rec_id,
        "contract_no": req.contract_no,
        "client_name": req.client_name,
        "amount": req.amount,
        "currency": req.currency,
        "invoice_no": req.invoice_no,
        "status": "generated",
        "created_at": now_iso(),
    }
    return {"status": "success", "reconciliation": rec}


@router.get("/reconcile/{rec_id}")
def get_reconciliation(rec_id: int, accept_language: str = Depends(get_accept_language)):
    rec = fetch_one("SELECT * FROM reconciliations WHERE id=%s", (rec_id,))
    if not rec:
        raise HTTPException(status_code=404, detail=get_message(accept_language, "not_found"))

    title_map = {
        "zh-CN": "对账单",
        "en-US": "Statement of Account",
        "ja-JP": "照合明細書",
    }
    title = title_map.get(accept_language, title_map["zh-CN"])
    return {"status": "success", "title": title, "data": rec}


def _normalize_fee_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _fee_record_key(order_no: Any, material_no: Any, heat_no: Any, heat_treatment_batch_no: Any) -> tuple[str, str, str, str]:
    return (
        _normalize_fee_text(order_no),
        _normalize_fee_text(material_no),
        _normalize_fee_text(heat_no),
        _normalize_fee_text(heat_treatment_batch_no),
    )


class TestFeeRecord(BaseModel):
    model_config = {"extra": "ignore"}

    id: str | None = None
    test_date: str
    material_no: str
    drawing_no: str | None = None
    specification: str | None = None
    material: str | None = None
    quantity: Any = None
    remark: str | None = None
    order_no: str
    item_no: Any = None
    heat_no: str
    heat_treatment_batch_no: str
    
    yield_strength: str | None = None
    tensile_strength: str | None = None
    elongation: str | None = None
    reduction_of_area: str | None = None
    hardness: str | None = None
    impact_work: str | None = None
    
    product_unit_price: Any = None
    test_qty_impact: Any = None
    test_qty_tensile: Any = None
    sample_fee_impact: Any = None
    sample_fee_tensile: Any = None
    test_fee_impact: Any = None
    test_fee_tensile: Any = None

class SaveTestFeeRequest(BaseModel):
    records: list[TestFeeRecord]


def _to_yymmdd(date_text: str) -> str:
    compact = date_text.replace("-", "")
    return compact[2:] if len(compact) == 8 else compact


def _extract_test_date_from_batch_no(batch_no: str | None) -> str | None:
    """从热处理批号提取试验日期，统一返回 YYMMDD。"""
    if not batch_no:
        return None

    normalized = batch_no.strip().upper()
    if "-" in normalized:
        # MY + yyyymmdd + -n，例如 MY20260131-1 -> 260131
        match = re.match(r"^MY(\d{4})(\d{2})(\d{2})-\d+", normalized)
        if not match:
            return None
        return f"{match.group(1)[2:]}{match.group(2)}{match.group(3)}"

    # MY + yymmdd + nn，例如 MY26012101 -> 260121
    match = re.match(r"^MY(\d{2})(\d{2})(\d{2})\d+", normalized)
    if not match:
        return None
    return f"{match.group(1)}{match.group(2)}{match.group(3)}"


DEFAULT_TEST_QTY_IMPACT = 6
DEFAULT_TEST_QTY_TENSILE = 1


def _default_test_qty(value: Any, fallback: int) -> int:
    if value is None or value == "":
        return fallback
    return int(value)


def _normalize_saved_fee_row(row: dict) -> dict:
    row["id"] = str(row["id"])
    row["heat_no"] = _normalize_fee_text(row.get("heat_no"))
    row["heat_treatment_batch_no"] = _normalize_fee_text(row.get("heat_treatment_batch_no"))
    row["order_no"] = _normalize_fee_text(row.get("order_no"))
    row["material_no"] = _normalize_fee_text(row.get("material_no"))
    row["drawing_no"] = _normalize_fee_text(row.get("drawing_no"))
    if row.get("item_no") is None:
        row["item_no"] = ""
    row["test_qty_impact"] = _default_test_qty(row.get("test_qty_impact"), DEFAULT_TEST_QTY_IMPACT)
    row["test_qty_tensile"] = _default_test_qty(row.get("test_qty_tensile"), DEFAULT_TEST_QTY_TENSILE)
    row["has_test_data"] = all(
        row.get(field) not in (None, "")
        for field in (
            "yield_strength",
            "tensile_strength",
            "elongation",
            "reduction_of_area",
            "hardness",
            "impact_work",
        )
    )
    return row


@router.post("/test-fee/extract-dates")
def extract_test_dates():
    orders = fetch_all(
        """
        SELECT id, heat_treatment_batch_no, test_date
        FROM order_details
        WHERE heat_no IS NOT NULL
          AND heat_no <> ''
          AND heat_treatment_batch_no IS NOT NULL
          AND heat_treatment_batch_no <> ''
        """
    )
    count = 0
    updates = []

    for order in orders:
        test_date = _extract_test_date_from_batch_no(order["heat_treatment_batch_no"])
        if test_date and order.get("test_date") != test_date:
            updates.append((test_date, order['id']))
            count += 1
            
    if updates:
        execute_many("UPDATE order_details SET test_date = %s WHERE id = %s", updates)
        
    return {"status": "success", "message": f"试验日期提取成功！共更新 {count} 条数据。", "count": count}


@router.get("/test-fee/records")
def get_test_fee_records(start_date: str, end_date: str):
    # start_date and end_date are like '2026-01-01'. We need YYMMDD format.
    format_s = _to_yymmdd(start_date)
    format_e = _to_yymmdd(end_date)
    
    # 1. Get order details in date range
    orders = fetch_all(
        "SELECT id, material_no, drawing_no, spec_model as specification, material, quantity, remark1 as remark, order_no, item_no, heat_no, heat_treatment_batch_no, test_date "
        "FROM order_details WHERE test_date >= %s AND test_date <= %s",
        (format_s, format_e)
    )
    
    if not orders:
        return {"status": "success", "data": []}
        
    # 2. Get trial records
    trial_records = fetch_all(
        "SELECT id, heat_no, batch_no, material_no, mech_yield_strength, mech_tensile_strength, mech_elongation, mech_reduction_area, "
        "mech_hardness_1, mech_hardness_2, mech_hardness_3, mech_impact_test "
        "FROM heat_treatment_trial_records "
        "ORDER BY id ASC"
    )
    
    # 3. Get existing test fee records
    existing_fees = fetch_all(
        "SELECT * FROM test_fee_records WHERE test_date >= %s AND test_date <= %s",
        (format_s, format_e)
    )
    existing_map = {
        _fee_record_key(f["order_no"], f["material_no"], f["heat_no"], f["heat_treatment_batch_no"]): f
        for f in existing_fees
    }
    
    result = []
    for order in orders:
        key = _fee_record_key(
            order["order_no"],
            order["material_no"],
            order.get("heat_no"),
            order.get("heat_treatment_batch_no"),
        )
        if key in existing_map:
            result.append(_normalize_saved_fee_row(existing_map[key]))
            continue
            
        # Match trial records
        matched_trials = [r for r in trial_records if r['heat_no'] == order['heat_no'] and r['batch_no'] == order['heat_treatment_batch_no']]
        selected_trial = None
        
        if len(matched_trials) == 1:
            selected_trial = matched_trials[0]
        elif len(matched_trials) > 1:
            for r in reversed(matched_trials):
                if r['material_no'] == order['material_no']:
                    selected_trial = r
                    break
            if not selected_trial:
                selected_trial = matched_trials[-1]
                
        has_test_data = selected_trial is not None
        
        hardness = ""
        if selected_trial:
            h_vals = [selected_trial.get(f'mech_hardness_{i}') for i in range(1, 4)]
            hardness = "/".join([str(v) for v in h_vals if v])
            
        row = {
            "id": f"new_{order['id']}",
            "test_date": order['test_date'],
            "material_no": order['material_no'],
            "drawing_no": order.get('drawing_no') or "",
            "specification": order['specification'],
            "material": order['material'],
            "quantity": order['quantity'],
            "remark": order['remark'],
            "order_no": order['order_no'],
            "item_no": order.get('item_no') if order.get('item_no') is not None else "",
            "heat_no": order['heat_no'],
            "heat_treatment_batch_no": order['heat_treatment_batch_no'],
            "has_test_data": has_test_data,
            "yield_strength": selected_trial['mech_yield_strength'] if selected_trial else "",
            "tensile_strength": selected_trial['mech_tensile_strength'] if selected_trial else "",
            "elongation": selected_trial['mech_elongation'] if selected_trial else "",
            "reduction_of_area": selected_trial['mech_reduction_area'] if selected_trial else "",
            "hardness": hardness,
            "impact_work": selected_trial['mech_impact_test'] if selected_trial else "",
            "product_unit_price": "",
            "test_qty_impact": DEFAULT_TEST_QTY_IMPACT,
            "test_qty_tensile": DEFAULT_TEST_QTY_TENSILE,
            "sample_fee_impact": "",
            "sample_fee_tensile": "",
            "test_fee_impact": "",
            "test_fee_tensile": ""
        }
        result.append(row)
        
    return {"status": "success", "data": result}


@router.get("/test-fee/saved-records")
def get_saved_test_fee_records(start_date: str, end_date: str):
    format_s = _to_yymmdd(start_date)
    format_e = _to_yymmdd(end_date)
    rows = fetch_all(
        "SELECT * FROM test_fee_records WHERE test_date >= %s AND test_date <= %s ORDER BY test_date, order_no, material_no, id",
        (format_s, format_e),
    )
    return {"status": "success", "data": [_normalize_saved_fee_row(row) for row in rows]}


@router.post("/test-fee/save")
def save_test_fee_records(req: SaveTestFeeRequest):
    if not req.records:
        return {"status": "success", "message": "没有需要保存的数据"}
        
    query = """
    INSERT INTO test_fee_records (
        test_date, material_no, drawing_no, specification, material, quantity, remark, 
        order_no, item_no, heat_no, heat_treatment_batch_no, 
        yield_strength, tensile_strength, elongation, reduction_of_area, hardness, impact_work,
        product_unit_price, test_qty_impact, test_qty_tensile, 
        sample_fee_impact, sample_fee_tensile, test_fee_impact, test_fee_tensile
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, 
        %s, %s, %s, %s, 
        %s, %s, %s, %s, %s, %s,
        %s, %s, %s, 
        %s, %s, %s, %s
    ) ON DUPLICATE KEY UPDATE 
        test_date = VALUES(test_date),
        drawing_no = VALUES(drawing_no),
        specification = VALUES(specification),
        material = VALUES(material),
        quantity = VALUES(quantity),
        remark = VALUES(remark),
        item_no = VALUES(item_no),
        yield_strength = VALUES(yield_strength),
        tensile_strength = VALUES(tensile_strength),
        elongation = VALUES(elongation),
        reduction_of_area = VALUES(reduction_of_area),
        hardness = VALUES(hardness),
        impact_work = VALUES(impact_work),
        product_unit_price = VALUES(product_unit_price),
        test_qty_impact = VALUES(test_qty_impact),
        test_qty_tensile = VALUES(test_qty_tensile),
        sample_fee_impact = VALUES(sample_fee_impact),
        sample_fee_tensile = VALUES(sample_fee_tensile),
        test_fee_impact = VALUES(test_fee_impact),
        test_fee_tensile = VALUES(test_fee_tensile)
    """
    
    def parse_float(val):
        if val == "" or val is None:
            return None
        return float(val)
        
    def parse_int(val, default: int | None = None):
        if val == "" or val is None:
            return default
        return int(val)

    def parse_quantity(val):
        if val == "" or val is None:
            return None
        return int(float(val))
        
    def parse_item_no(val):
        if val == "" or val is None:
            return None
        return int(float(val))

    params = []
    for r in req.records:
        params.append((
            _normalize_fee_text(r.test_date),
            _normalize_fee_text(r.material_no),
            _normalize_fee_text(r.drawing_no),
            r.specification,
            r.material,
            parse_quantity(r.quantity),
            r.remark,
            _normalize_fee_text(r.order_no),
            parse_item_no(r.item_no),
            _normalize_fee_text(r.heat_no),
            _normalize_fee_text(r.heat_treatment_batch_no),
            r.yield_strength, r.tensile_strength, r.elongation, r.reduction_of_area, r.hardness, r.impact_work,
            parse_float(r.product_unit_price),
            parse_int(r.test_qty_impact, DEFAULT_TEST_QTY_IMPACT),
            parse_int(r.test_qty_tensile, DEFAULT_TEST_QTY_TENSILE),
            parse_float(r.sample_fee_impact), parse_float(r.sample_fee_tensile), parse_float(r.test_fee_impact), parse_float(r.test_fee_tensile)
        ))
        
    execute_many(query, params)
    count = len(req.records)
    return {
        "status": "success",
        "message": f"已保存 {count} 条试验费用记录",
        "saved_count": count,
    }
