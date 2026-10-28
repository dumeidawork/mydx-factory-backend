"""工作台待办聚合：一次返回合同/图纸/物料未读列表，避免前端并行打多个接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.api.v1.contract_archives import get_pending_amount_confirm_items, get_pending_takeover_items
from app.api.v1.dalian_econtract import get_pending_dalian_contract_defer_items
from app.api.v1.drawing_archives import get_pending_review_items, get_pending_revise_items
from app.services.material_align import get_pending_material_drawing_items, get_pending_material_price_items
from app.api.v1.france_auto_packing_qty_maps import get_pending_qty_map_dn_notice_items
from app.api.v1.dalian_shipping_reservations import get_dalian_shipping_progress
from app.services.typing_notice import (
    confirm_typing_notice,
    generate_from_typing_notice,
    get_pending_typing_notice_items,
)

router = APIRouter(prefix="/workbench", tags=["工作台"])


@router.get("/pending")
def list_pending(user: Annotated[CurrentUser, Depends(get_current_user)]):
    items = get_pending_takeover_items(user.id)
    amount_items = get_pending_amount_confirm_items(user.id)
    drawing_review_items = get_pending_review_items(user.id)
    drawing_revise_items = get_pending_revise_items(user.id)
    material_drawing_items = get_pending_material_drawing_items(user.id)
    material_price_items = get_pending_material_price_items(user.id)
    typing_notice_items = get_pending_typing_notice_items(user.id)
    dalian_contract_defer_items = get_pending_dalian_contract_defer_items(user.id)
    qty_map_dn_items = get_pending_qty_map_dn_notice_items(user.id)
    dalian_shipping_progress = get_dalian_shipping_progress(user.id)
    return {
        "items": items,
        "amount_items": amount_items,
        "drawing_review_items": drawing_review_items,
        "drawing_revise_items": drawing_revise_items,
        "material_drawing_items": material_drawing_items,
        "material_price_items": material_price_items,
        "typing_notice_items": typing_notice_items,
        "dalian_contract_defer_items": dalian_contract_defer_items,
        "qty_map_dn_items": qty_map_dn_items,
        "dalian_shipping_progress": dalian_shipping_progress,
        "count": (
            len(items)
            + len(amount_items)
            + len(drawing_review_items)
            + len(drawing_revise_items)
            + len(material_drawing_items)
            + len(material_price_items)
            + len(typing_notice_items)
            + len(dalian_contract_defer_items)
            + len(qty_map_dn_items)
        ),
    }


class TypingNoticeGenerateRequest(BaseModel):
    order_detail_ids: list[int] = Field(default_factory=list)


@router.post("/typing-notices/{notice_id}/confirm")
def confirm_typing_notice_item(
    notice_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    return confirm_typing_notice(notice_id, user)


@router.post("/typing-notices/{notice_id}/generate")
def generate_typing_notice_item(
    notice_id: int,
    req: TypingNoticeGenerateRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    return generate_from_typing_notice(notice_id, req.order_detail_ids, user)
