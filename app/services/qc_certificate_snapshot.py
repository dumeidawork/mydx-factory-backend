"""质保书快照：按业务六元组（order_detail_id + 五元组）UPSERT 已签发质保书完整数据。"""
from __future__ import annotations

import json
from typing import Any

from app.core.database import execute, fetch_all, fetch_one
from app.services.dalian_certificate_allocation import normalize_item_no_key


def _normalize_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def snapshot_business_key_from_row(row: dict) -> dict[str, Any]:
    """从 order_details 行或快照行提取业务六元组（含 order_details.id）。"""
    item_no = row.get("item_no")
    # 快照行优先 order_detail_id；订单明细行用主键 id
    detail_id = _parse_optional_int(row.get("order_detail_id"))
    if detail_id is None:
        detail_id = _parse_optional_int(row.get("id"))
    return {
        "order_detail_id": detail_id,
        "order_no": _normalize_str(row.get("order_no", "")),
        "material_no": _normalize_str(row.get("material_no", "")),
        "item_no": item_no if item_no not in (None, "") else None,
        "item_no_key": normalize_item_no_key(item_no),
        "heat_no": _normalize_str(row.get("heat_no", "")),
        "heat_treatment_batch_no": _normalize_str(row.get("heat_treatment_batch_no", "")),
    }


def snapshot_business_key_from_record(record: dict) -> dict[str, Any]:
    """从 qc_records 记录提取业务六元组（delivery_content 可补炉号/批号）。"""
    delivery_rows = record.get("delivery_content") or []
    delivery = delivery_rows[0] if delivery_rows else {}
    item_no = record.get("item_no")
    if item_no in (None, "") and delivery.get("item_no") not in (None, ""):
        item_no = delivery.get("item_no")
    return {
        "order_detail_id": _parse_optional_int(record.get("order_detail_id")),
        "order_no": _normalize_str(record.get("order_no", "")),
        "material_no": _normalize_str(record.get("material_no", "")),
        "item_no": item_no if item_no not in (None, "") else None,
        "item_no_key": normalize_item_no_key(item_no if item_no not in (None, "") else record.get("item_no_key")),
        "heat_no": _normalize_str(delivery.get("raw_material_no", record.get("heat_no", ""))),
        "heat_treatment_batch_no": _normalize_str(
            delivery.get("batch_no", record.get("heat_treatment_batch_no", ""))
        ),
    }


def _resolve_certificate_type(record: dict) -> str:
    base = record.get("base_info") or {}
    cert_type = _normalize_str(base.get("certificate_type", "")).lower()
    if cert_type in ("dalian", "10-dl-qc", "大连"):
        return "dalian"
    explicit = _normalize_str(record.get("certificate_type", "")).lower()
    if explicit == "dalian":
        return "dalian"
    order_no = _normalize_str(record.get("order_no", ""))
    if order_no.startswith("4"):
        return "dalian"
    if order_no.startswith("2"):
        return "france"
    return "france"


def _row_to_snapshot_payload(row: dict) -> dict[str, Any]:
    base_info = row.get("base_info") or {}
    if isinstance(base_info, str):
        base_info = json.loads(base_info)
    delivery = row.get("delivery_content") or []
    if isinstance(delivery, str):
        delivery = json.loads(delivery)
    mechanical = row.get("mechanical_tests") or []
    if isinstance(mechanical, str):
        mechanical = json.loads(mechanical)
    chemical = row.get("chemical_analysis") or []
    if isinstance(chemical, str):
        chemical = json.loads(chemical)
    return {
        "base_info": base_info,
        "delivery_content": delivery,
        "mechanical_tests": mechanical,
        "chemical_analysis": chemical,
    }


def upsert_snapshot_from_record(
    record: dict,
    *,
    order_detail_id: int | None = None,
    qc_record_id: int | None = None,
    certificate_path: str = "",
) -> int:
    """从 qc_records 结构 UPSERT 快照，返回 snapshot id。"""
    key = snapshot_business_key_from_record(record)
    payload = _row_to_snapshot_payload(record)
    cert_type = _resolve_certificate_type(record)
    detail_id = order_detail_id
    if detail_id is None and record.get("order_detail_id") is not None:
        detail_id = int(record["order_detail_id"])
    if detail_id is None:
        detail_id = key.get("order_detail_id")
    rec_id = qc_record_id if qc_record_id is not None else record.get("id")
    path = _normalize_str(certificate_path or record.get("certificate_path", ""))

    existing = None
    if detail_id is not None:
        existing = fetch_one(
            "SELECT id FROM qc_certificate_snapshots WHERE order_detail_id=%s",
            (int(detail_id),),
        )
    if not existing:
        five_tuple_params = (
            key["order_no"],
            key["material_no"],
            key["item_no_key"],
            key["heat_no"],
            key["heat_treatment_batch_no"],
        )
        if detail_id is not None:
            existing = fetch_one(
                """
                SELECT id FROM qc_certificate_snapshots
                WHERE order_no=%s AND material_no=%s AND item_no_key=%s
                  AND heat_no=%s AND heat_treatment_batch_no=%s
                  AND (order_detail_id IS NULL OR order_detail_id=%s)
                """,
                (*five_tuple_params, int(detail_id)),
            )
        else:
            existing = fetch_one(
                """
                SELECT id FROM qc_certificate_snapshots
                WHERE order_no=%s AND material_no=%s AND item_no_key=%s
                  AND heat_no=%s AND heat_treatment_batch_no=%s
                """,
                five_tuple_params,
            )

    if existing:
        execute(
            """
            UPDATE qc_certificate_snapshots SET
                order_detail_id=%s,
                item_no=%s,
                certificate_type=%s,
                base_info=%s,
                delivery_content=%s,
                mechanical_tests=%s,
                chemical_analysis=%s,
                latest_qc_record_id=%s,
                certificate_path=%s,
                updated_at=NOW()
            WHERE id=%s
            """,
            (
                detail_id,
                key["item_no"],
                cert_type,
                json.dumps(payload["base_info"], ensure_ascii=False),
                json.dumps(payload["delivery_content"], ensure_ascii=False),
                json.dumps(payload["mechanical_tests"], ensure_ascii=False),
                json.dumps(payload["chemical_analysis"], ensure_ascii=False),
                rec_id,
                path,
                int(existing["id"]),
            ),
        )
        return int(existing["id"])

    return execute(
        """
        INSERT INTO qc_certificate_snapshots (
            order_detail_id, order_no, material_no, item_no, item_no_key,
            heat_no, heat_treatment_batch_no, certificate_type,
            base_info, delivery_content, mechanical_tests, chemical_analysis,
            latest_qc_record_id, certificate_path
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            detail_id,
            key["order_no"],
            key["material_no"],
            key["item_no"],
            key["item_no_key"],
            key["heat_no"],
            key["heat_treatment_batch_no"],
            cert_type,
            json.dumps(payload["base_info"], ensure_ascii=False),
            json.dumps(payload["delivery_content"], ensure_ascii=False),
            json.dumps(payload["mechanical_tests"], ensure_ascii=False),
            json.dumps(payload["chemical_analysis"], ensure_ascii=False),
            rec_id,
            path,
        ),
    )


def find_snapshot_for_order_detail(row: dict) -> dict | None:
    """按 order_details 业务六元组查找快照（优先 order_detail_id）。"""
    key = snapshot_business_key_from_row(row)
    detail_id = key.get("order_detail_id")
    snap = None
    if detail_id is not None:
        snap = fetch_one(
            "SELECT * FROM qc_certificate_snapshots WHERE order_detail_id=%s",
            (int(detail_id),),
        )
    if not snap:
        five_tuple_params = (
            key["order_no"],
            key["material_no"],
            key["item_no_key"],
            key["heat_no"],
            key["heat_treatment_batch_no"],
        )
        if detail_id is not None:
            snap = fetch_one(
                """
                SELECT * FROM qc_certificate_snapshots
                WHERE order_no=%s AND material_no=%s AND item_no_key=%s
                  AND heat_no=%s AND heat_treatment_batch_no=%s
                  AND (order_detail_id IS NULL OR order_detail_id=%s)
                """,
                (*five_tuple_params, int(detail_id)),
            )
        else:
            snap = fetch_one(
                """
                SELECT * FROM qc_certificate_snapshots
                WHERE order_no=%s AND material_no=%s AND item_no_key=%s
                  AND heat_no=%s AND heat_treatment_batch_no=%s
                """,
                five_tuple_params,
            )
    if not snap:
        return None
    return snapshot_row_to_dict(snap)


def snapshot_row_to_dict(row: dict) -> dict[str, Any]:
    """DB 行转 API 字典。"""
    base_info = row.get("base_info") or {}
    if isinstance(base_info, str):
        base_info = json.loads(base_info)
    delivery = row.get("delivery_content") or []
    if isinstance(delivery, str):
        delivery = json.loads(delivery)
    mechanical = row.get("mechanical_tests") or []
    if isinstance(mechanical, str):
        mechanical = json.loads(mechanical)
    chemical = row.get("chemical_analysis") or []
    if isinstance(chemical, str):
        chemical = json.loads(chemical)
    generated_at = row.get("generated_at")
    updated_at = row.get("updated_at")
    return {
        "id": int(row["id"]),
        "snapshot_id": int(row["id"]),
        "order_detail_id": row.get("order_detail_id"),
        "order_no": row.get("order_no", ""),
        "material_no": row.get("material_no", ""),
        "item_no": row.get("item_no"),
        "heat_no": row.get("heat_no", ""),
        "heat_treatment_batch_no": row.get("heat_treatment_batch_no", ""),
        "certificate_type": row.get("certificate_type", ""),
        "base_info": base_info,
        "delivery_content": delivery,
        "mechanical_tests": mechanical,
        "chemical_analysis": chemical,
        "latest_qc_record_id": row.get("latest_qc_record_id"),
        "certificate_path": row.get("certificate_path", "") or "",
        "factory_date": _normalize_str(base_info.get("date", "")),
        "certificate_no": _normalize_str(base_info.get("certificate_no", "")),
        "generated_at": str(generated_at) if generated_at else "",
        "updated_at": str(updated_at) if updated_at else "",
        "has_generated_certificate": True,
    }


def query_snapshots(
    *,
    region: str = "",
    order_no: str = "",
    material_no: str = "",
    status: str = "",
) -> list[dict[str, Any]]:
    """查询快照列表，按 region / order_no / material_no / order_details 状态 过滤。"""
    conditions: list[str] = []
    params: list[Any] = []
    join_order_details = bool(_normalize_str(status))
    table_prefix = "s." if join_order_details else ""

    if order_no:
        conditions.append(f"{table_prefix}order_no = %s")
        params.append(order_no.strip())
    if material_no:
        conditions.append(f"LOWER({table_prefix}material_no) LIKE %s")
        params.append(f"%{material_no.strip().lower()}%")
    region_text = region.strip().lower()
    if region_text == "dalian":
        conditions.append(f"{table_prefix}order_no LIKE '4%'")
    elif region_text == "france":
        conditions.append(f"{table_prefix}order_no LIKE '2%'")
    status_text = _normalize_str(status)
    if status_text:
        conditions.append("od.order_status = %s")
        params.append(status_text)

    where = " AND ".join(conditions) if conditions else "1=1"
    if join_order_details:
        sql = f"""
            SELECT s.*
            FROM qc_certificate_snapshots s
            INNER JOIN order_details od ON od.id = s.order_detail_id
            WHERE {where}
            ORDER BY s.updated_at DESC, s.id DESC
        """
    else:
        sql = f"SELECT * FROM qc_certificate_snapshots WHERE {where} ORDER BY updated_at DESC, id DESC"
    rows = fetch_all(sql, tuple(params))
    return [snapshot_row_to_dict(r) for r in rows]


def get_snapshot_by_id(snapshot_id: int) -> dict | None:
    row = fetch_one("SELECT * FROM qc_certificate_snapshots WHERE id=%s", (int(snapshot_id),))
    if not row:
        return None
    return snapshot_row_to_dict(row)


def enrich_order_detail_with_snapshot(row: dict) -> dict:
    """为 order_details 行附加快照标记字段。"""
    snap = find_snapshot_for_order_detail(row)
    enriched = dict(row)
    if snap:
        enriched["has_generated_certificate"] = True
        enriched["snapshot_id"] = snap["id"]
        enriched["snapshot_date"] = snap.get("factory_date") or ""
        enriched["snapshot_certificate_no"] = snap.get("certificate_no") or ""
    else:
        enriched["has_generated_certificate"] = False
        enriched["snapshot_id"] = None
        enriched["snapshot_date"] = None
        enriched["snapshot_certificate_no"] = None
    return enriched
