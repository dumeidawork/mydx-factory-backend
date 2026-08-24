"""工作台待办聚合：一次返回合同/图纸/物料未读列表，避免前端并行打多个接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.api.v1.contract_archives import get_pending_amount_confirm_items, get_pending_takeover_items
from app.api.v1.drawing_archives import get_pending_review_items, get_pending_revise_items
from app.services.material_align import get_pending_material_drawing_items, get_pending_material_price_items

router = APIRouter(prefix="/workbench", tags=["工作台"])


@router.get("/pending")
def list_pending(user: Annotated[CurrentUser, Depends(get_current_user)]):
    items = get_pending_takeover_items(user.id)
    amount_items = get_pending_amount_confirm_items(user.id)
    drawing_review_items = get_pending_review_items(user.id)
    drawing_revise_items = get_pending_revise_items(user.id)
    material_drawing_items = get_pending_material_drawing_items(user.id)
    material_price_items = get_pending_material_price_items(user.id)
    return {
        "items": items,
        "amount_items": amount_items,
        "drawing_review_items": drawing_review_items,
        "drawing_revise_items": drawing_revise_items,
        "material_drawing_items": material_drawing_items,
        "material_price_items": material_price_items,
        "count": (
            len(items)
            + len(amount_items)
            + len(drawing_review_items)
            + len(drawing_revise_items)
            + len(material_drawing_items)
            + len(material_price_items)
        ),
    }
