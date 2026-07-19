"""从 qc_records 历史记录回填 qc_certificate_snapshots。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import fetch_all
from app.services.dalian_certificate_allocation import normalize_item_no_key
from app.services.qc_certificate_snapshot import upsert_snapshot_from_record


def _normalize_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _business_key_from_record(row: dict) -> tuple:
    base_info = row.get("base_info") or {}
    if isinstance(base_info, str):
        base_info = json.loads(base_info)
    delivery_rows = row.get("delivery_content") or []
    if isinstance(delivery_rows, str):
        delivery_rows = json.loads(delivery_rows)
    delivery = delivery_rows[0] if delivery_rows else {}
    item_no = row.get("item_no")
    if item_no in (None, "") and delivery.get("item_no") not in (None, ""):
        item_no = delivery.get("item_no")
    item_no_key = normalize_item_no_key(item_no if item_no not in (None, "") else row.get("item_no_key"))
    heat_no = _normalize_str(delivery.get("raw_material_no", ""))
    batch_no = _normalize_str(delivery.get("batch_no", ""))
    return (
        _normalize_str(row.get("order_no", "")),
        _normalize_str(row.get("material_no", "")),
        item_no_key,
        heat_no,
        batch_no,
        int(row["id"]),
        int(row.get("order_detail_id") or 0),
        _normalize_str(row.get("certificate_path", "")),
    )


def main() -> None:
    rows = fetch_all(
        """
        SELECT id, order_no, material_no, order_detail_id, item_no, item_no_key,
               base_info, delivery_content, mechanical_tests, chemical_analysis,
               certificate_path, certificate_type, created_at
        FROM qc_records
        WHERE certificate_path IS NOT NULL AND certificate_path != ''
        ORDER BY id ASC
        """
    )
    latest: dict[tuple, dict] = {}
    for row in rows:
        key_parts = _business_key_from_record(row)
        biz_key = key_parts[:5]
        if not key_parts[0] or not key_parts[1]:
            continue
        latest[biz_key] = row

    upserted = 0
    for row in latest.values():
        record = {
            "id": int(row["id"]),
            "order_no": row["order_no"],
            "material_no": row["material_no"],
            "order_detail_id": row.get("order_detail_id"),
            "item_no": row.get("item_no"),
            "item_no_key": row.get("item_no_key"),
            "base_info": json.loads(row["base_info"]) if isinstance(row.get("base_info"), str) else row.get("base_info"),
            "delivery_content": json.loads(row["delivery_content"])
            if isinstance(row.get("delivery_content"), str)
            else row.get("delivery_content"),
            "mechanical_tests": json.loads(row["mechanical_tests"])
            if isinstance(row.get("mechanical_tests"), str)
            else row.get("mechanical_tests"),
            "chemical_analysis": json.loads(row["chemical_analysis"])
            if isinstance(row.get("chemical_analysis"), str)
            else row.get("chemical_analysis"),
            "certificate_path": row.get("certificate_path", ""),
            "certificate_type": row.get("certificate_type"),
        }
        upsert_snapshot_from_record(
            record,
            order_detail_id=int(row["order_detail_id"]) if row.get("order_detail_id") else None,
            qc_record_id=int(row["id"]),
            certificate_path=_normalize_str(row.get("certificate_path", "")),
        )
        upserted += 1

    print(f"backfill complete: {upserted} snapshots upserted from {len(rows)} qc_records rows")


if __name__ == "__main__":
    main()
