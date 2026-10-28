"""法国自动装箱：对照表/箱单数量分箱、混装与 D/d 物理约束。"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.api.v1.drawing_flange_type_maps import load_flange_type_map, lookup_flange_type
from app.api.v1.pallet_archives import load_pallet_archives
from app.core.database import fetch_all, fetch_one
from app.services.material_drawing_link import ensure_material_drawing_archive_column, resolve_drawing_archive

CLEAR_L_MM = 1150
CLEAR_W_MM = 750
OD_WOOD_THRESHOLD = 575
WEIGHT_LIMIT_KG = 500.0
DEFAULT_BOX_WEIGHT = 20.0
DEFAULT_FENCE_MM = 200
GENERIC_PALLETS = ("实木托盘", "欧式托盘", "周转托盘")
DEFAULT_PALLET = "实木托盘"
DN_LARGE = 450
PAIR_QTY = 2
_DN_NUM = re.compile(r"(\d+)")


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _dn_number(raw: str) -> int:
    text = _norm(raw).upper()
    match = _DN_NUM.search(text)
    return int(match.group(1)) if match else 0


def _dn_key(raw: str) -> str:
    num = _dn_number(raw)
    return f"DN{num}" if num else _norm(raw).upper()


def material_family(material: str) -> str:
    text = _norm(material).upper().replace(" ", "")
    if "A105" in text or "碳钢" in _norm(material) or text in {"CS", "CARBON"}:
        return "carbon"
    if "316" in text:
        return "ss316"
    if "304" in text or "不锈钢" in _norm(material):
        return "ss304"
    return text or "unknown"


def _family_class(family: str) -> str:
    if family.startswith("ss"):
        return "ss"
    return family


def can_mix(a: "WorkItem", b: "WorkItem") -> bool:
    if a.material_no and a.material_no == b.material_no:
        return True
    same_dn = _dn_key(a.dn) == _dn_key(b.dn) and bool(_dn_key(a.dn))
    if same_dn and a.flange_type and b.flange_type and a.flange_type != b.flange_type:
        return False
    fa, fb = material_family(a.material), material_family(b.material)
    if "carbon" in {fa, fb} and ("ss304" in {fa, fb} or "ss316" in {fa, fb} or _family_class(fa) == "ss" or _family_class(fb) == "ss"):
        if {fa, fb} != {"ss304", "ss316"} and ("carbon" in {fa, fb}):
            return False
    if not same_dn:
        if a.flange_type and b.flange_type and a.flange_type != b.flange_type:
            return False
        if _family_class(fa) != _family_class(fb) and {fa, fb} != {"ss304", "ss316"}:
            return False
        return True
    return _family_class(fa) == _family_class(fb) or {fa, fb} <= {"ss304", "ss316", "unknown"}


def high_neck_n(od_d: float, mouth_n: float) -> int | None:
    if od_d <= 0:
        return None
    best = 1
    for n in range(1, 40):
        gap = (od_d - mouth_n) / 2.0 * (n - 1)
        length = od_d * n - gap
        if length <= CLEAR_L_MM:
            best = n
        else:
            break
    return best


@dataclass
class WorkItem:
    item_id: int
    pos: int
    material_no: str
    spec: str
    model: str
    material: str
    dn: str
    drawing_no: str
    quantity: int
    remaining: int
    unit_weight: float
    units_per_pallet: float
    nb_of_pallets: float
    flange_type: str
    qty_per_pallet: int
    qty_source: str
    pallet_name: str
    pallet_type_label: str
    length_mm: int
    width_mm: int
    height_mm: int
    box_weight: float
    od_d: float | None
    mouth_n: float | None
    drawing_archive_id: int | None
    warnings: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    large_dn: bool = False


@dataclass
class BoxLine:
    item_id: int
    pos: int
    material_no: str
    spec: str
    model: str
    material: str
    dn: str
    flange_type: str
    quantity: int
    unit_weight: float
    qty_per_pallet: int
    qty_source: str
    pallet_name: str
    pallet_type_label: str
    length_mm: int
    width_mm: int
    height_mm: int
    box_weight: float
    od_d: float | None
    mouth_n: float | None
    drawing_archive_id: int | None
    drawing_no: str
    warnings: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)


@dataclass
class Box:
    box_no: int
    lines: list[BoxLine] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    mixed: bool = False
    odd_tail: bool = False


def list_headers_for_order(order_no: str) -> list[dict[str, Any]]:
    key = _norm(order_no)
    if not key:
        return []
    return fetch_all(
        """
        SELECT l.id, l.source_file_name, l.factory_order_no, l.customer, l.departure_date,
               l.updated_at, l.created_at
        FROM france_customer_packing_lists l
        WHERE EXISTS (
            SELECT 1 FROM france_customer_packing_list_items i
            WHERE i.list_id = l.id AND TRIM(i.po)=%s
        )
        ORDER BY l.updated_at DESC, l.id DESC
        """,
        (key,),
    )


def pick_list_id(order_no: str, list_id: int | None = None) -> dict[str, Any] | None:
    headers = list_headers_for_order(order_no)
    if not headers:
        return None
    if list_id:
        for row in headers:
            if int(row["id"]) == int(list_id):
                return row
    return headers[0]


def load_list_items(list_id: int, order_no: str) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT *
        FROM france_customer_packing_list_items
        WHERE list_id=%s AND TRIM(po)=%s
        ORDER BY COALESCE(pos, 999999), seq, id
        """,
        (int(list_id), _norm(order_no)),
    )


def existing_packing_count(order_no: str) -> int:
    row = fetch_one(
        "SELECT COUNT(*) AS n FROM packing_details WHERE order_no=%s",
        (_norm(order_no),),
    )
    return int(row["n"] or 0) if row else 0


def factory_order_no_for(order_no: str, header: dict[str, Any] | None) -> str:
    from_header = _norm((header or {}).get("factory_order_no"))
    if from_header:
        return from_header
    row = fetch_one(
        "SELECT factory_order_no FROM order_details WHERE order_no=%s LIMIT 1",
        (_norm(order_no),),
    )
    return _norm((row or {}).get("factory_order_no"))


def _load_qty_maps(material_nos: list[str], table: str = "france_auto_packing_qty_maps") -> dict[str, list[dict[str, Any]]]:
    if table not in {"france_auto_packing_qty_maps", "dalian_auto_packing_qty_maps"}:
        return {}
    if not material_nos:
        return {}
    exists = fetch_one(
        """
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name=%s
        LIMIT 1
        """,
        (table,),
    )
    if not exists:
        return {}
    placeholders = ", ".join(["%s"] * len(material_nos))
    rows = fetch_all(
        f"""
        SELECT material_no, qty_per_pallet, pallet_type, length_mm, width_mm, height_mm
        FROM {table}
        WHERE material_no IN ({placeholders})
        """,
        tuple(material_nos),
    )
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        out[_norm(row.get("material_no"))].append(row)
    return out


def _choose_qty_map(
    maps: list[dict[str, Any]],
    selected_pallet: str,
    archives: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if not maps:
        return None
    if selected_pallet:
        for row in maps:
            if _norm(row.get("pallet_type")) == selected_pallet:
                return row
    with_dims: list[dict[str, Any]] = []
    for row in maps:
        name = _norm(row.get("pallet_type"))
        archive = archives.get(name)
        if archive and (archive.get("length_mm") or row.get("length_mm")):
            with_dims.append(row)
    if with_dims:
        return with_dims[0]
    return maps[0]


def _apply_pallet(
    pallet_name: str,
    archives: dict[str, dict[str, Any]],
    length_mm: int = 0,
    width_mm: int = 0,
    height_mm: int = 0,
    box_weight: float = 0.0,
) -> tuple[str, str, int, int, int, float, bool]:
    name = _norm(pallet_name) or DEFAULT_PALLET
    archive = archives.get(name)
    missing_standard = False
    if archive:
        label = _norm(archive.get("pallet_type")) or name
        length_mm = length_mm or int(archive.get("length_mm") or 0)
        width_mm = width_mm or int(archive.get("width_mm") or 0)
        height_mm = height_mm or int(archive.get("height_mm") or 0)
        if box_weight <= 0:
            box_weight = _as_float(archive.get("box_weight_kg"), DEFAULT_BOX_WEIGHT)
        return name, label, length_mm, width_mm, height_mm, box_weight or DEFAULT_BOX_WEIGHT, False
    if name in GENERIC_PALLETS:
        return name, name, length_mm, width_mm, height_mm, box_weight or DEFAULT_BOX_WEIGHT, False
    missing_standard = True
    return DEFAULT_PALLET, DEFAULT_PALLET, length_mm, width_mm, height_mm, box_weight or DEFAULT_BOX_WEIGHT, missing_standard


def enrich_items(
    raw_items: list[dict[str, Any]],
    *,
    selected_pallets: dict[str, str] | None = None,
    qty_map_table: str = "france_auto_packing_qty_maps",
) -> list[WorkItem]:
    selected_pallets = selected_pallets or {}
    ensure_material_drawing_archive_column()
    archives = {row["pallet_name"]: row for row in load_pallet_archives() if row.get("pallet_name")}
    flange_map = load_flange_type_map()
    material_nos = [_norm(r.get("material_no")) for r in raw_items if _norm(r.get("material_no"))]
    qty_maps = _load_qty_maps(material_nos, qty_map_table)
    material_ids = fetch_all(
        f"""
        SELECT material_no, drawing_no, drawing_archive_id
        FROM materials
        WHERE material_no IN ({", ".join(["%s"] * len(material_nos))})
        """,
        tuple(material_nos),
    ) if material_nos else []
    archive_by_material: dict[str, int] = {}
    for row in material_ids:
        key = _norm(row.get("material_no"))
        if key and row.get("drawing_archive_id") and key not in archive_by_material:
            archive_by_material[key] = int(row["drawing_archive_id"])

    items: list[WorkItem] = []
    for raw in raw_items:
        material_no = _norm(raw.get("material_no"))
        drawing_no = _norm(raw.get("drawing_no"))
        spec = _norm(raw.get("spec") or raw.get("dn"))
        model = _norm(raw.get("model"))
        material = _norm(raw.get("material"))
        dn = _dn_key(spec or _norm(raw.get("dn")))
        flange = lookup_flange_type(drawing_no, flange_map)
        qty = _as_int(raw.get("quantity"))
        unit_weight = _as_float(raw.get("unit_weight"))
        warnings: list[str] = []
        blocking: list[str] = []
        if not spec:
            warnings.append("规格为空")
        if not model:
            warnings.append("型号为空")
        if not material:
            warnings.append("材质为空")
        if unit_weight <= 0:
            warnings.append("单重为空")
        if not flange:
            warnings.append("法兰类型未对照")

        maps = qty_maps.get(material_no, [])
        chosen = _choose_qty_map(maps, selected_pallets.get(material_no, ""), archives)
        qty_per = 0
        qty_source = "手填"
        pallet_name = selected_pallets.get(material_no, "")
        length_mm = width_mm = height_mm = 0
        box_weight = DEFAULT_BOX_WEIGHT
        if chosen:
            qty_per = _as_int(chosen.get("qty_per_pallet"))
            qty_source = "对照表"
            pallet_name = pallet_name or _norm(chosen.get("pallet_type"))
            length_mm = _as_int(chosen.get("length_mm"))
            width_mm = _as_int(chosen.get("width_mm"))
            height_mm = _as_int(chosen.get("height_mm"))
        elif _as_float(raw.get("units_per_pallet")) > 0:
            qty_per = int(_as_float(raw.get("units_per_pallet")))
            qty_source = "客户箱单"
        elif _dn_number(dn) >= DN_LARGE:
            qty_per = PAIR_QTY
            qty_source = "成对启发"
            warnings.append("大口径无对照，按每托 2 件")
        else:
            blocking.append("无每托数量")

        pallet_name, pallet_label, length_mm, width_mm, height_mm, box_weight, missing_pallet = _apply_pallet(
            pallet_name, archives, length_mm, width_mm, height_mm, box_weight
        )
        if missing_pallet or (qty_source == "手填" and not chosen):
            warnings.append("托盘/尺寸无对照，默认实木")

        archive = resolve_drawing_archive(
            material_no=material_no,
            drawing_no=drawing_no,
            drawing_archive_id=archive_by_material.get(material_no),
        )
        od_d = _as_float(archive.get("od_d"), 0.0) if archive else 0.0
        mouth_n = _as_float(archive.get("mouth_n"), 0.0) if archive else 0.0
        if not archive or od_d <= 0:
            warnings.append("无 D/d，跳过外径约束")
            od_d = None
            mouth_n = None
        else:
            od_d = od_d
            mouth_n = mouth_n or 0.0

        if od_d and od_d > OD_WOOD_THRESHOLD:
            pallet_name, pallet_label, length_mm, width_mm, height_mm, box_weight, _ = _apply_pallet(
                "实木托盘", archives, 0, 0, 0, box_weight
            )
            warnings.append("外径>575，改实木托盘，尺寸请手填或选自建档")

        items.append(
            WorkItem(
                item_id=_as_int(raw.get("id")),
                pos=_as_int(raw.get("pos")),
                material_no=material_no,
                spec=spec,
                model=model,
                material=material,
                dn=dn,
                drawing_no=drawing_no,
                quantity=qty,
                remaining=qty,
                unit_weight=unit_weight,
                units_per_pallet=_as_float(raw.get("units_per_pallet")),
                nb_of_pallets=_as_float(raw.get("nb_of_pallets")),
                flange_type=flange,
                qty_per_pallet=qty_per,
                qty_source=qty_source,
                pallet_name=pallet_name,
                pallet_type_label=pallet_label,
                length_mm=length_mm,
                width_mm=width_mm,
                height_mm=height_mm,
                box_weight=box_weight,
                od_d=od_d,
                mouth_n=mouth_n,
                drawing_archive_id=int(archive["id"]) if archive and archive.get("id") else None,
                warnings=warnings,
                blocking=blocking,
                large_dn=_dn_number(dn) >= DN_LARGE,
            )
        )
    return items


def _take_from_material(items: list[WorkItem], material_no: str, need: int) -> list[tuple[WorkItem, int]]:
    taken: list[tuple[WorkItem, int]] = []
    left = need
    for item in sorted(items, key=lambda x: (x.pos, x.item_id)):
        if item.material_no != material_no or item.remaining <= 0 or left <= 0:
            continue
        use = min(item.remaining, left)
        item.remaining -= use
        left -= use
        taken.append((item, use))
    return taken


def _lines_from_taken(taken: list[tuple[WorkItem, int]]) -> list[BoxLine]:
    lines: list[BoxLine] = []
    for item, qty in taken:
        if qty <= 0:
            continue
        lines.append(
            BoxLine(
                item_id=item.item_id,
                pos=item.pos,
                material_no=item.material_no,
                spec=item.spec,
                model=item.model,
                material=item.material,
                dn=item.dn,
                flange_type=item.flange_type,
                quantity=qty,
                unit_weight=item.unit_weight,
                qty_per_pallet=item.qty_per_pallet,
                qty_source=item.qty_source,
                pallet_name=item.pallet_name,
                pallet_type_label=item.pallet_type_label,
                length_mm=item.length_mm,
                width_mm=item.width_mm,
                height_mm=item.height_mm,
                box_weight=item.box_weight,
                od_d=item.od_d,
                mouth_n=item.mouth_n,
                drawing_archive_id=item.drawing_archive_id,
                drawing_no=item.drawing_no,
                warnings=list(item.warnings),
                blocking=list(item.blocking),
            )
        )
    lines.sort(key=lambda x: (-x.quantity, x.pos))
    return lines


def _box_net_weight(box: Box) -> float:
    return sum(line.quantity * line.unit_weight for line in box.lines)


def _box_weight_value(box: Box) -> float:
    return next((line.box_weight for line in box.lines if line.box_weight > 0), DEFAULT_BOX_WEIGHT)


def _apply_physical(box: Box) -> None:
    qty = sum(line.quantity for line in box.lines)
    if qty % 2 == 1:
        box.odd_tail = True
        box.warnings.append("尾箱奇数件")
    materials = {line.material_no for line in box.lines}
    box.mixed = len(materials) > 1
    od_d = next((line.od_d for line in box.lines if line.od_d), None)
    mouth_n = next((line.mouth_n for line in box.lines if line.mouth_n is not None), None)
    if od_d:
        n = high_neck_n(od_d, mouth_n or 0)
        if n:
            layer_cap = n * n
            layers = max(1, math.ceil(qty / layer_cap)) if layer_cap else 1
            extra = max(0, layers - 1) * DEFAULT_FENCE_MM
            height = max((line.height_mm for line in box.lines), default=0)
            if extra > DEFAULT_FENCE_MM or (height and height + extra > height + DEFAULT_FENCE_MM):
                box.warnings.append("需再加围挡")
            elif layers > 1:
                for line in box.lines:
                    if not line.height_mm:
                        line.height_mm = DEFAULT_FENCE_MM * layers
                box.warnings.append(f"单层上限 {n}×{n}，已按 {layers} 层估算高度")
    total = _box_net_weight(box) + _box_weight_value(box)
    if total > WEIGHT_LIMIT_KG:
        box.blocking.append(f"单托总重 {total:.1f}kg 超过 {int(WEIGHT_LIMIT_KG)}kg")


def split_overweight(boxes: list[Box]) -> list[Box]:
    result: list[Box] = []
    for box in boxes:
        current = box
        while True:
            _apply_physical(current)
            overweight = any("超过" in msg for msg in current.blocking)
            if not overweight:
                result.append(current)
                break
            if len(current.lines) == 1 and current.lines[0].quantity <= 1:
                result.append(current)
                break
            last = current.lines[-1]
            move = 1 if last.quantity <= 1 else max(1, last.quantity // 2)
            last.quantity -= move
            if last.quantity <= 0:
                current.lines.pop()
            new_box = Box(box_no=0, lines=[])
            leftover = BoxLine(**{**last.__dict__, "quantity": move})
            new_box.lines.append(leftover)
            if not current.lines:
                current = new_box
                continue
            current.blocking = [msg for msg in current.blocking if "超过" not in msg]
            _apply_physical(current)
            result.append(current)
            current = new_box
    return result


def build_boxes(items: list[WorkItem]) -> list[Box]:
    boxes: list[Box] = []
    materials = []
    seen: set[str] = set()
    for item in sorted(items, key=lambda x: (x.pos, x.item_id)):
        if item.material_no not in seen:
            seen.add(item.material_no)
            materials.append(item.material_no)

    for material_no in materials:
        group = [x for x in items if x.material_no == material_no]
        per = next((x.qty_per_pallet for x in group if x.qty_per_pallet > 0), 0)
        if per <= 0:
            continue
        if per % 2 == 1:
            per = per
        while sum(x.remaining for x in group) >= per:
            taken = _take_from_material(items, material_no, per)
            boxes.append(Box(box_no=0, lines=_lines_from_taken(taken)))

    leftovers = [x for x in items if x.remaining > 0]
    leftovers.sort(key=lambda x: (-x.remaining, x.pos, x.item_id))
    while leftovers:
        seed = leftovers[0]
        if seed.qty_per_pallet <= 0 and seed.remaining > 0:
            take_qty = seed.remaining
        else:
            take_qty = seed.remaining
        seed.remaining = 0
        taken = [(seed, take_qty)]
        members = [seed]
        if not seed.large_dn or take_qty < (seed.qty_per_pallet or PAIR_QTY):
            for other in leftovers[1:]:
                if other.remaining <= 0:
                    continue
                if seed.large_dn and other.material_no != seed.material_no and take_qty >= (seed.qty_per_pallet or PAIR_QTY):
                    continue
                if not all(can_mix(other, m) for m in members):
                    continue
                use = other.remaining
                other.remaining = 0
                taken.append((other, use))
                members.append(other)
        boxes.append(Box(box_no=0, lines=_lines_from_taken(taken)))
        leftovers = [x for x in leftovers if x.remaining > 0]
        leftovers.sort(key=lambda x: (-x.remaining, x.pos, x.item_id))

    boxes = split_overweight(boxes)
    for idx, box in enumerate(boxes, start=1):
        box.box_no = idx
        _apply_physical(box)
        box.blocking = list(dict.fromkeys(box.blocking))
        box.warnings = list(dict.fromkeys(box.warnings))
    return boxes


def flatten_rows(boxes: list[Box], order_no: str, list_id: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    idx = 0
    for box in boxes:
        for line in box.lines:
            idx += 1
            total_weight = round(line.quantity * line.unit_weight, 2)
            gross = round(total_weight + line.box_weight, 2) if line is box.lines[0] else total_weight
            warnings = list(dict.fromkeys([*line.warnings, *box.warnings]))
            blocking = list(dict.fromkeys([*line.blocking, *box.blocking]))
            rows.append(
                {
                    "row_key": f"{box.box_no}-{line.pos}-{line.material_no}-{idx}",
                    "order_no": order_no,
                    "list_id": list_id,
                    "item_id": line.item_id,
                    "box_no": box.box_no,
                    "pos": line.pos,
                    "entry_no": line.pos,
                    "material_no": line.material_no,
                    "spec": line.spec,
                    "standard": line.model,
                    "model": line.model,
                    "material": line.material,
                    "dn": line.dn,
                    "flange_type": line.flange_type,
                    "remark1": line.flange_type,
                    "quantity": line.quantity,
                    "unit_weight": line.unit_weight,
                    "total_weight": total_weight,
                    "gross_weight": gross if line is box.lines[0] else 0,
                    "qty_per_pallet": line.qty_per_pallet,
                    "qty_source": line.qty_source,
                    "pallet_name": line.pallet_name,
                    "pallet_type": line.pallet_type_label,
                    "packing_remark": line.pallet_type_label,
                    "box_length": line.length_mm,
                    "box_width": line.width_mm,
                    "box_height": line.height_mm,
                    "box_weight": line.box_weight,
                    "od_d": line.od_d,
                    "mouth_n": line.mouth_n,
                    "drawing_archive_id": line.drawing_archive_id,
                    "drawing_no": line.drawing_no,
                    "mixed": box.mixed,
                    "odd_tail": box.odd_tail,
                    "warnings": warnings,
                    "blocking": blocking,
                    "need_confirm": bool(blocking),
                }
            )
    return rows


def build_confirm(header: dict[str, Any], items: list[WorkItem], extra_lists: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, int] = defaultdict(int)
    map_hit = 0
    map_miss = 0
    dd_hit = 0
    for item in items:
        merged[item.material_no] += item.quantity
        if item.qty_source == "对照表":
            map_hit += 1
        else:
            map_miss += 1
        if item.od_d:
            dd_hit += 1
    qty_preview = []
    seen: set[str] = set()
    for item in items:
        if item.material_no in seen:
            continue
        seen.add(item.material_no)
        qty_preview.append(
            {
                "material_no": item.material_no,
                "qty_per_pallet": item.qty_per_pallet,
                "qty_source": item.qty_source,
                "pallet_name": item.pallet_name,
                "has_dd": bool(item.od_d),
            }
        )
    expected_pallets = 0.0
    calc_pallets = 0
    for item in items:
        expected_pallets += item.nb_of_pallets
    return {
        "list_id": int(header["id"]),
        "source_file_name": _norm(header.get("source_file_name")),
        "factory_order_no": _norm(header.get("factory_order_no")),
        "customer": _norm(header.get("customer")),
        "departure_date": str(header.get("departure_date") or ""),
        "item_count": len(items),
        "merged_qty": int(sum(merged.values())),
        "merged_materials": len(merged),
        "map_hit": map_hit,
        "map_miss": map_miss,
        "dd_hit": dd_hit,
        "dd_miss": len(items) - dd_hit,
        "qty_preview": qty_preview,
        "customer_pallet_hint": expected_pallets,
        "lists": [
            {
                "id": int(row["id"]),
                "source_file_name": _norm(row.get("source_file_name")),
                "updated_at": str(row.get("updated_at") or ""),
            }
            for row in extra_lists
        ],
        "missing_fields": [item.material_no for item in items if "规格为空" in item.warnings or "单重为空" in item.warnings],
    }


def preview_order(order_no: str, list_id: int | None = None) -> dict[str, Any]:
    from app.api.v1.customer_packing import _ensure_tables

    _ensure_tables()
    headers = list_headers_for_order(order_no)
    header = pick_list_id(order_no, list_id)
    if not header:
        return {
            "status": "empty",
            "detail": "未找到该订单号的法国客户箱单",
            "confirm": None,
            "rows": [],
            "has_existing_packing": existing_packing_count(order_no) > 0,
            "lists": [],
            "factory_order_no": factory_order_no_for(order_no, None),
        }
    raw_items = load_list_items(int(header["id"]), order_no)
    items = enrich_items(raw_items)
    boxes = build_boxes(items)
    rows = flatten_rows(boxes, order_no, int(header["id"]))
    calc_pallets = len(boxes)
    confirm = build_confirm(header, enrich_items(raw_items), headers)
    confirm["calc_pallets"] = calc_pallets
    if confirm["customer_pallet_hint"] and abs(confirm["customer_pallet_hint"] - calc_pallets) > 0.2:
        confirm["pallet_count_mismatch"] = True
    else:
        confirm["pallet_count_mismatch"] = False
    confirm["factory_order_no"] = factory_order_no_for(order_no, header)
    return {
        "status": "success",
        "confirm": confirm,
        "rows": rows,
        "has_existing_packing": existing_packing_count(order_no) > 0,
        "lists": confirm["lists"],
        "factory_order_no": confirm["factory_order_no"],
        "pallet_options": [
            {"value": name, "label": name, "generic": True} for name in GENERIC_PALLETS
        ]
        + [
            {
                "value": row["pallet_name"],
                "label": row["pallet_name"],
                "generic": False,
                "length_mm": row.get("length_mm"),
                "width_mm": row.get("width_mm"),
                "height_mm": row.get("height_mm"),
                "box_weight_kg": row.get("box_weight_kg"),
                "pallet_type": row.get("pallet_type"),
            }
            for row in load_pallet_archives()
            if row.get("pallet_name")
        ],
    }


def renumber_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mapping: dict[int, int] = {}
    next_no = 1
    out: list[dict[str, Any]] = []
    for row in rows:
        old = _as_int(row.get("box_no"))
        if old not in mapping:
            mapping[old] = next_no
            next_no += 1
        cloned = dict(row)
        cloned["box_no"] = mapping[old]
        out.append(cloned)
    return out


def annotate_posted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_as_int(row.get("box_no"))].append(row)
    out: list[dict[str, Any]] = []
    for box_no, group in grouped.items():
        net = sum(_as_float(r.get("unit_weight")) * _as_int(r.get("quantity")) for r in group)
        box_weight = _as_float(next((r.get("box_weight") for r in group if _as_float(r.get("box_weight")) > 0), DEFAULT_BOX_WEIGHT))
        mixed = len({_norm(r.get("material_no")) for r in group}) > 1
        qty = sum(_as_int(r.get("quantity")) for r in group)
        blocking: list[str] = []
        warnings: list[str] = []
        if net + box_weight > WEIGHT_LIMIT_KG:
            blocking.append(f"单托总重 {net + box_weight:.1f}kg 超过 {int(WEIGHT_LIMIT_KG)}kg")
        if any(_as_int(r.get("qty_per_pallet")) <= 0 for r in group):
            blocking.append("无每托数量")
        if qty % 2 == 1:
            warnings.append("尾箱奇数件")
        for row in group:
            cloned = dict(row)
            cloned["mixed"] = mixed
            cloned["total_weight"] = round(_as_int(row.get("quantity")) * _as_float(row.get("unit_weight")), 2)
            cloned["warnings"] = list(dict.fromkeys([*list(row.get("warnings") or []), *warnings]))
            cloned["blocking"] = list(dict.fromkeys([*list(row.get("blocking") or []), *blocking]))
            cloned["need_confirm"] = bool(cloned["blocking"])
            cloned["box_no"] = box_no
            out.append(cloned)
    out.sort(key=lambda x: (_as_int(x.get("box_no")), _as_int(x.get("pos")), _norm(x.get("material_no"))))
    return out


def main_box_no_by_item(rows: list[dict[str, Any]]) -> dict[int, tuple[int, list[int]]]:
    qty_by: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        item_id = _as_int(row.get("item_id"))
        if not item_id:
            continue
        qty_by[item_id][_as_int(row.get("box_no"))] += _as_int(row.get("quantity"))
    result: dict[int, tuple[int, list[int]]] = {}
    for item_id, boxes in qty_by.items():
        main = max(boxes.items(), key=lambda kv: (kv[1], -kv[0]))[0]
        result[item_id] = (main, sorted(boxes.keys()))
    return result
