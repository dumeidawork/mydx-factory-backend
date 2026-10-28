"""大连自动装箱的物料-数量/托。表独立，首次打开时从法国对照复制现有数据。"""
from fastapi import APIRouter

from app.api.v1.france_auto_packing_qty_maps import (
    align_dalian_qty_map_dn,
    delete_dalian_qty_map,
    list_dalian_qty_map_logs,
    list_dalian_qty_maps,
    lookup_dalian_material_dn,
    save_dalian_qty_maps,
)

router = APIRouter(prefix="/dalian-auto-packing-qty-maps", tags=["大连自动装箱-物料数量/托"])

router.add_api_route("", list_dalian_qty_maps, methods=["GET"])
router.add_api_route("/logs", list_dalian_qty_map_logs, methods=["GET"])
router.add_api_route("/material-dn", lookup_dalian_material_dn, methods=["GET"])
router.add_api_route("/save", save_dalian_qty_maps, methods=["POST"])
router.add_api_route("/align-dn", align_dalian_qty_map_dn, methods=["POST"])
router.add_api_route("/{row_id}", delete_dalian_qty_map, methods=["DELETE"])
