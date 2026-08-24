"""物料待办缺口检测与图纸/价格拆分对齐到物料表。"""
from __future__ import annotations

from typing import Any

from app.core.database import execute, fetch_all, fetch_one


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _table_exists(table: str) -> bool:
    row = fetch_one(
        """
        SELECT 1 AS n
        FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = %s
        LIMIT 1
        """,
        (table,),
    )
    return bool(row)


def _ensure_materials() -> None:
    from app.api.v1.workflow import _ensure_materials_table

    _ensure_materials_table()


def _safe_float(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _same_material_info(material_no: str) -> tuple[bool, str]:
    if not material_no:
        return False, ""
    row = fetch_one(
        """
        SELECT drawing_no FROM materials
        WHERE material_no=%s
        ORDER BY id
        LIMIT 1
        """,
        (material_no,),
    )
    if not row:
        return False, ""
    return True, _norm(row.get("drawing_no"))


def _missing_hint(material_no: str, same_material_no: bool, existing_drawing_no: str) -> str:
    if same_material_no:
        return f"请上传新版本物料图纸版本信息（原物料信息：{material_no}+{existing_drawing_no or '空'}）"
    return f"请上传新物料信息（{material_no}）"


def _decorate_item(item: dict[str, Any]) -> dict[str, Any]:
    material_no = _norm(item.get("material_no"))
    same, existing_drawing = _same_material_info(material_no)
    item["same_material_no"] = same
    item["existing_drawing_no"] = existing_drawing
    item["hint"] = _missing_hint(material_no, same, existing_drawing)
    return item


def _material_exists(material_no: str, drawing_candidates: list[str], standard_no: str = "") -> bool:
    return _find_material(material_no, drawing_candidates, standard_no) is not None


def _find_material(material_no: str, drawing_candidates: list[str], standard_no: str = "") -> dict | None:
    key_no = _norm(material_no)
    if not key_no:
        return None
    seen: set[str] = set()
    for drawing_no in drawing_candidates:
        key_drawing = _norm(drawing_no)
        if not key_drawing or key_drawing in seen:
            continue
        seen.add(key_drawing)
        row = fetch_one(
            """
            SELECT id, material_no, drawing_no, standard_no, spec, model, spec_model, material, unit_weight,
                   product_unit_price, france_agreement_price, dalian_agreement_price
            FROM materials
            WHERE material_no=%s AND COALESCE(drawing_no, '')=%s
            ORDER BY id
            LIMIT 1
            """,
            (key_no, key_drawing),
        )
        if row:
            return row
    key_standard = _norm(standard_no)
    if key_standard:
        row = fetch_one(
            """
            SELECT id, material_no, drawing_no, standard_no, spec, model, spec_model, material, unit_weight,
                   product_unit_price, france_agreement_price, dalian_agreement_price
            FROM materials
            WHERE material_no=%s
              AND COALESCE(drawing_no, '') IN ('', '-')
              AND COALESCE(standard_no, '')=%s
            ORDER BY id
            LIMIT 1
            """,
            (key_no, key_standard),
        )
        if row:
            return row
    return None


def get_pending_material_drawing_items(user_id: int) -> list[dict]:
    _ensure_materials()
    if not _table_exists("drawing_archives"):
        return []
    rows = fetch_all(
        """
        SELECT
            d.id AS source_id,
            TRIM(d.material_no) AS material_no,
            TRIM(d.drawing_no) AS drawing_no,
            TRIM(COALESCE(d.drawing_rev_no, '')) AS drawing_rev_no,
            TRIM(COALESCE(d.standard_no, '')) AS standard_no,
            d.uploaded_by_name,
            d.updated_by_name,
            d.uploaded_by_user_id,
            d.updated_by_user_id,
            d.uploaded_at,
            d.updated_at
        FROM drawing_archives d
        WHERE TRIM(COALESCE(d.material_no, '')) <> ''
          AND (d.uploaded_by_user_id=%s OR d.updated_by_user_id=%s)
        ORDER BY d.updated_at DESC, d.id DESC
        """,
        (user_id, user_id),
    )
    merged: dict[tuple[str, str], dict] = {}
    for row in rows:
        material_no = _norm(row.get("material_no"))
        drawing_no = _norm(row.get("drawing_no"))
        drawing_rev_no = _norm(row.get("drawing_rev_no"))
        standard_no = _norm(row.get("standard_no"))
        candidates = [drawing_no, drawing_rev_no]
        if not drawing_rev_no:
            candidates.append("-")
        if _material_exists(material_no, candidates, standard_no):
            continue
        key = (drawing_no or drawing_rev_no or "-", material_no)
        if key in merged:
            continue
        merged[key] = _decorate_item(
            {
                "key": f"drawing::{key[0]}::{material_no}",
                "source": "drawing",
                "source_id": int(row["source_id"]),
                "drawing_no": key[0],
                "material_no": material_no,
                "drawing_rev_no": drawing_rev_no,
                "standard_no": standard_no,
                "uploaded_by": _norm(row.get("uploaded_by_name")),
                "updated_by": _norm(row.get("updated_by_name")),
                "uploaded_at": str(row.get("uploaded_at") or ""),
                "updated_at": str(row.get("updated_at") or ""),
            }
        )
    return list(merged.values())


def get_pending_material_price_items(user_id: int) -> list[dict]:
    _ensure_materials()
    parts: list[dict] = []
    for region, table in (("france", "price_split_france"), ("dalian", "price_split_dalian")):
        if not _table_exists(table):
            continue
        rows = fetch_all(
            f"""
            SELECT
                id AS source_id,
                TRIM(material_no) AS material_no,
                TRIM(COALESCE(drawing_no, '')) AS drawing_no,
                TRIM(COALESCE(drawing_code, '')) AS drawing_code,
                created_by,
                updated_by,
                created_by_user_id,
                updated_by_user_id,
                updated_at,
                created_at
            FROM {table}
            WHERE is_valid=1
              AND TRIM(COALESCE(material_no, '')) <> ''
              AND (created_by_user_id=%s OR updated_by_user_id=%s)
            ORDER BY updated_at DESC, id DESC
            """,
            (user_id, user_id),
        )
        for row in rows:
            parts.append({**row, "region": region})

    merged: dict[tuple[str, str], dict] = {}
    for row in parts:
        material_no = _norm(row.get("material_no"))
        drawing_no = _norm(row.get("drawing_no"))
        drawing_code = _norm(row.get("drawing_code"))
        if _material_exists(material_no, [drawing_no, drawing_code]):
            continue
        key = (drawing_no, material_no)
        existing = merged.get(key)
        region = _norm(row.get("region"))
        if existing:
            regions = {item for item in existing["region"].split(",") if item}
            regions.add(region)
            existing["region"] = ",".join(sorted(regions))
            continue
        merged[key] = _decorate_item(
            {
                "key": f"price_split::{drawing_no}::{material_no}",
                "source": "price_split",
                "source_id": int(row["source_id"]),
                "region": region,
                "drawing_no": drawing_no,
                "material_no": material_no,
                "drawing_code": drawing_code,
                "uploaded_by": _norm(row.get("created_by")),
                "updated_by": _norm(row.get("updated_by")),
                "uploaded_at": str(row.get("created_at") or ""),
                "updated_at": str(row.get("updated_at") or ""),
            }
        )
    return list(merged.values())


def _load_drawing_for_align(material_no: str, drawing_no: str) -> dict | None:
    if not _table_exists("drawing_archives"):
        return None
    return fetch_one(
        """
        SELECT
            TRIM(material_no) AS material_no,
            TRIM(drawing_no) AS drawing_no,
            TRIM(COALESCE(drawing_rev_no, '')) AS drawing_rev_no,
            TRIM(COALESCE(standard_no, '')) AS standard_no,
            spec, model, spec_model, material, unit_weight
        FROM drawing_archives
        WHERE TRIM(COALESCE(material_no, ''))=%s
          AND (
            TRIM(drawing_no)=%s
            OR TRIM(COALESCE(drawing_rev_no, ''))=%s
          )
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (material_no, drawing_no, drawing_no),
    )


def _load_price_row(region: str, material_no: str, drawing_no: str, part_no: str = "") -> dict | None:
    table = "price_split_france" if region == "france" else "price_split_dalian"
    if not _table_exists(table):
        return None
    key_no = _norm(material_no)
    key_drawing = _norm(drawing_no)
    key_part = _norm(part_no)
    base_sql = f"""
        SELECT net_price, tax_inclusive_price, drawing_no, drawing_code
        FROM {table}
        WHERE is_valid=1
          AND TRIM(material_no)=%s
          AND (
            TRIM(COALESCE(drawing_no, ''))=%s
            OR TRIM(COALESCE(drawing_code, ''))=%s
          )
    """
    if region == "dalian" and key_part:
        row = fetch_one(
            base_sql + " AND TRIM(COALESCE(part_no, ''))=%s ORDER BY updated_at DESC, id DESC LIMIT 1",
            (key_no, key_drawing, key_drawing, key_part),
        )
        if row:
            return row
    return fetch_one(
        base_sql + " ORDER BY updated_at DESC, id DESC LIMIT 1",
        (key_no, key_drawing, key_drawing),
    )


def load_order_prices_from_split(material_no: str, drawing_no: str, part_no: str = "") -> dict[str, float]:
    """订单录入取价：只读价格拆分有效行。

    大连协议价/产品单价 = 大连最终报价；
    法国协议价 = 法国最终报价；法国产品单价 = 法国含税单价。
    """
    france = _load_price_row("france", material_no, drawing_no)
    dalian = _load_price_row("dalian", material_no, drawing_no, part_no)
    france_net = _safe_float(france.get("net_price")) if france else None
    france_tax = _safe_float(france.get("tax_inclusive_price")) if france else None
    dalian_net = _safe_float(dalian.get("net_price")) if dalian else None
    return {
        "france_agreement_price": france_net if france_net is not None else 0.0,
        "france_tax_inclusive_price": france_tax if france_tax is not None else 0.0,
        "dalian_agreement_price": dalian_net if dalian_net is not None else 0.0,
    }


def _align_one(drawing_no: str, material_no: str, operator: str) -> dict[str, Any]:
    key_no = _norm(material_no)
    key_drawing = _norm(drawing_no)
    drawing = _load_drawing_for_align(key_no, key_drawing)
    france = _load_price_row("france", key_no, key_drawing)
    dalian = _load_price_row("dalian", key_no, key_drawing)
    candidates = [key_drawing]
    if drawing:
        candidates.extend([_norm(drawing.get("drawing_no")), _norm(drawing.get("drawing_rev_no"))])
    if france:
        candidates.extend([_norm(france.get("drawing_no")), _norm(france.get("drawing_code"))])
    if dalian:
        candidates.extend([_norm(dalian.get("drawing_no")), _norm(dalian.get("drawing_code"))])
    standard_no = _norm(drawing.get("standard_no")) if drawing else ""
    material = _find_material(key_no, candidates, standard_no)
    if not material:
        same, existing_drawing = _same_material_info(key_no)
        return {
            "ok": False,
            "drawing_no": key_drawing,
            "material_no": key_no,
            "same_material_no": same,
            "existing_drawing_no": existing_drawing,
            "hint": _missing_hint(key_no, same, existing_drawing),
        }

    sets: list[str] = []
    params: list[Any] = []
    fields: list[str] = []

    if drawing:
        mapping = (
            ("spec", _norm(drawing.get("spec"))),
            ("model", _norm(drawing.get("model"))),
            ("spec_model", _norm(drawing.get("spec_model"))),
            ("material", _norm(drawing.get("material"))),
            ("standard_no", _norm(drawing.get("standard_no"))),
        )
        for column, value in mapping:
            if value:
                sets.append(f"{column}=%s")
                params.append(value)
                fields.append(column)
        weight = _safe_float(drawing.get("unit_weight"))
        if weight is not None and weight > 0:
            sets.append("unit_weight=%s")
            params.append(weight)
            fields.append("unit_weight")

    has_france_price = False
    if france:
        net_price = _safe_float(france.get("net_price"))
        tax_price = _safe_float(france.get("tax_inclusive_price"))
        if net_price is not None:
            sets.append("france_agreement_price=%s")
            params.append(net_price)
            fields.append("france_agreement_price")
            has_france_price = True
        if tax_price is not None:
            sets.append("product_unit_price=%s")
            params.append(tax_price)
            fields.append("product_unit_price")
            has_france_price = True

    if dalian:
        net_price = _safe_float(dalian.get("net_price"))
        if net_price is not None:
            sets.append("dalian_agreement_price=%s")
            params.append(net_price)
            fields.append("dalian_agreement_price")
            if not has_france_price:
                sets.append("product_unit_price=%s")
                params.append(net_price)
                fields.append("product_unit_price")

    if sets:
        sets.append("updated_by=%s")
        params.append(_norm(operator) or "system")
        sets.append("updated_at=NOW()")
        params.append(int(material["id"]))
        execute(f"UPDATE materials SET {', '.join(sets)} WHERE id=%s", tuple(params))

    return {
        "ok": True,
        "drawing_no": key_drawing,
        "material_no": key_no,
        "material_id": int(material["id"]),
        "fields": fields,
    }


def align_confirm(
    user_id: int,
    operator: str,
    keys: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _ensure_materials()
    selected = [
        {"drawing_no": _norm(item.get("drawing_no")), "material_no": _norm(item.get("material_no"))}
        for item in (keys or [])
        if _norm(item.get("material_no"))
    ]
    if not selected:
        pending = (*get_pending_material_drawing_items(user_id), *get_pending_material_price_items(user_id))
        seen: set[tuple[str, str]] = set()
        for item in pending:
            pair = (_norm(item.get("drawing_no")), _norm(item.get("material_no")))
            if not pair[1] or pair in seen:
                continue
            seen.add(pair)
            selected.append({"drawing_no": pair[0], "material_no": pair[1]})

    aligned: list[dict] = []
    missing: list[dict] = []
    for item in selected:
        result = _align_one(item["drawing_no"], item["material_no"], operator)
        if result.get("ok"):
            aligned.append(result)
        else:
            missing.append(result)
    return {
        "status": "success",
        "aligned_count": len(aligned),
        "missing_count": len(missing),
        "aligned": aligned,
        "missing": missing,
    }
