"""合同->生产->质检->发货主流程MVP API。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.database import execute, fetch_all, fetch_one
from app.services.in_memory_store import now_iso

router = APIRouter(prefix="/operations", tags=["主流程"])

FLOW_NODES = [
    "contract_received",
    "order_assigned",
    "cutting",
    "forging",
    "machining",
    "qc",
    "packaging",
    "shipping",
]


class CreateContractRequest(BaseModel):
    client_name: str
    drawing_type: str = "normal"
    total_amount: float = 0
    client_language: str = "zh-CN"


class CreateOrderRequest(BaseModel):
    contract_id: int
    material_type: str = "本厂钢材"


class AdvanceNodeRequest(BaseModel):
    order_id: int
    next_node: str
    operator: str
    remark: str = ""


@router.post("/contracts")
def create_contract(req: CreateContractRequest):
    contract_id = execute(
        """
        INSERT INTO contracts (client_name, drawing_type, total_amount, client_language, status)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (req.client_name, req.drawing_type, req.total_amount, req.client_language, "received"),
    )
    contract = {
        "id": contract_id,
        "client_name": req.client_name,
        "drawing_type": req.drawing_type,
        "total_amount": req.total_amount,
        "client_language": req.client_language,
        "status": "received",
        "created_at": now_iso(),
    }
    return {"status": "success", "contract": contract}


@router.post("/orders")
def create_order(req: CreateOrderRequest):
    contract = fetch_one("SELECT id FROM contracts WHERE id=%s", (req.contract_id,))
    if not contract:
        raise HTTPException(status_code=404, detail="contract not found")
    order_id = execute(
        """
        INSERT INTO production_orders (contract_id, material_type, current_node, status)
        VALUES (%s, %s, %s, %s)
        """,
        (req.contract_id, req.material_type, "order_assigned", "in_progress"),
    )
    order = {
        "id": order_id,
        "contract_id": req.contract_id,
        "material_type": req.material_type,
        "current_node": "order_assigned",
        "status": "in_progress",
        "created_at": now_iso(),
    }
    execute(
        "INSERT INTO production_events (order_id, node, operator, remark) VALUES (%s, %s, %s, %s)",
        (order_id, "order_assigned", "system", "订单创建"),
    )
    return {"status": "success", "order": order}


@router.post("/advance")
def advance_order(req: AdvanceNodeRequest):
    order = fetch_one("SELECT * FROM production_orders WHERE id=%s", (req.order_id,))
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    if req.next_node not in FLOW_NODES:
        raise HTTPException(status_code=400, detail=f"unsupported node: {req.next_node}")

    next_status = "done" if req.next_node == "shipping" else "in_progress"
    execute(
        "UPDATE production_orders SET current_node=%s, status=%s, updated_at=NOW() WHERE id=%s",
        (req.next_node, next_status, req.order_id),
    )
    event = {
        "order_id": req.order_id,
        "node": req.next_node,
        "operator": req.operator,
        "remark": req.remark,
        "created_at": now_iso(),
    }
    execute(
        "INSERT INTO production_events (order_id, node, operator, remark) VALUES (%s, %s, %s, %s)",
        (req.order_id, req.next_node, req.operator, req.remark),
    )
    order["current_node"] = req.next_node
    order["status"] = next_status
    return {"status": "success", "order": order, "event": event}


@router.get("/orders/{order_id}")
def get_order(order_id: int):
    order = fetch_one("SELECT * FROM production_orders WHERE id=%s", (order_id,))
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    timeline = fetch_all("SELECT order_id, node, operator, remark, created_at FROM production_events WHERE order_id=%s ORDER BY id", (order_id,))
    return {"status": "success", "order": order, "timeline": timeline}
