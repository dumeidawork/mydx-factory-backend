"""从已有 qc_records 回填大连证书编号分配表与日计数器。"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import fetch_all, get_db
from app.services.dalian_certificate_allocation import (
    format_certificate_no,
    normalize_item_no_key,
    parse_certificate_no,
)


def _is_dalian_record(base_info: dict) -> bool:
    cert_type = str(base_info.get("certificate_type", "")).lower()
    if cert_type in ("dalian", "10-dl-qc", "大连"):
        return True
    cert_no = str(base_info.get("certificate_no", ""))
    return cert_no.upper().startswith("ZYXMZ") and parse_certificate_no(cert_no) is not None


def main() -> None:
    rows = fetch_all(
        """
        SELECT id, order_no, material_no, order_detail_id, base_info, delivery_content,
               item_no_key, cert_date_yymmdd, cert_daily_seq
        FROM qc_records
        ORDER BY id
        """
    )
    # 优先按 order_detail_id；无明细 ID 时回退旧三元组
    business_best: dict[tuple, dict] = {}
    daily_max: dict[str, int] = defaultdict(int)

    for row in rows:
        base_info = json.loads(row["base_info"]) if row.get("base_info") else {}
        if not _is_dalian_record(base_info):
            continue

        delivery_rows = json.loads(row["delivery_content"]) if row.get("delivery_content") else []
        delivery = delivery_rows[0] if delivery_rows else {}
        item_no_key = row.get("item_no_key")
        if item_no_key is None:
            item_no_key = normalize_item_no_key(delivery.get("item_no"))

        cert_no = str(base_info.get("certificate_no", "")).strip()
        parsed = parse_certificate_no(cert_no)
        if parsed:
            yymmdd, seq = parsed
        elif row.get("cert_date_yymmdd") and row.get("cert_daily_seq"):
            yymmdd = str(row["cert_date_yymmdd"])
            seq = int(row["cert_daily_seq"])
            cert_no = format_certificate_no(yymmdd, seq)
        else:
            continue

        daily_max[yymmdd] = max(daily_max[yymmdd], seq)
        detail_id = row.get("order_detail_id")
        if detail_id is not None:
            key: tuple = ("detail", int(detail_id))
        else:
            key = ("legacy", row["order_no"], row["material_no"], int(item_no_key))
        current = business_best.get(key)
        if current is None or int(row["id"]) < int(current["record_id"]):
            business_best[key] = {
                "record_id": int(row["id"]),
                "order_detail_id": int(detail_id) if detail_id is not None else None,
                "order_no": row["order_no"],
                "material_no": row["material_no"],
                "item_no_key": int(item_no_key),
                "certificate_no": cert_no,
                "cert_date_yymmdd": yymmdd,
                "cert_daily_seq": seq,
            }

    inserted_alloc = 0
    updated_counters = 0
    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)
        for _key, payload in business_best.items():
            detail_id = payload["order_detail_id"]
            if detail_id is not None:
                cursor.execute(
                    """
                    SELECT id FROM dalian_certificate_allocations
                    WHERE order_detail_id=%s
                    LIMIT 1
                    """,
                    (detail_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT id FROM dalian_certificate_allocations
                    WHERE order_no=%s AND material_no=%s AND item_no_key=%s
                      AND order_detail_id IS NULL
                    LIMIT 1
                    """,
                    (payload["order_no"], payload["material_no"], payload["item_no_key"]),
                )
            if cursor.fetchone():
                continue
            cursor.execute(
                """
                INSERT INTO dalian_certificate_allocations (
                    order_detail_id, order_no, material_no, item_no_key,
                    certificate_no, cert_date_yymmdd, cert_daily_seq
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    detail_id,
                    payload["order_no"],
                    payload["material_no"],
                    payload["item_no_key"],
                    payload["certificate_no"],
                    payload["cert_date_yymmdd"],
                    payload["cert_daily_seq"],
                ),
            )
            inserted_alloc += 1

        for yymmdd, max_seq in daily_max.items():
            next_seq = max_seq + 1
            cursor.execute(
                """
                INSERT INTO dalian_certificate_daily_counters (cert_date_yymmdd, next_seq)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE next_seq = GREATEST(next_seq, VALUES(next_seq))
                """,
                (yymmdd, next_seq),
            )
            updated_counters += 1
        cursor.close()

    print(
        json.dumps(
            {
                "status": "success",
                "inserted_allocations": inserted_alloc,
                "updated_daily_counters": updated_counters,
                "business_keys": len(business_best),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
