"""大连质保书证书编号分配：按 YYMMDD 当日流水递增，按 order_detail_id 幂等复用。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.core.database import get_db


_DALIAN_CERT_PATTERN = re.compile(r"^ZYXMZ(\d{6})-(\d+)$", re.IGNORECASE)


def normalize_item_no_key(item_no: Any) -> int:
    if item_no in (None, ""):
        return 0
    try:
        return int(float(item_no))
    except (TypeError, ValueError):
        return 0


def parse_optional_order_detail_id(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_qc_date_text(date_text: str) -> datetime:
    text = str(date_text or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    parts = [int(p) for p in re.split(r"[-/]", text) if p.strip().isdigit()]
    if len(parts) >= 3:
        year, month, day = parts[0], parts[1], parts[2]
        if year < 100:
            year += 2000
        return datetime(year, month, day)
    return datetime.now()


def yymmdd_from_date_text(date_text: str) -> str:
    return parse_qc_date_text(date_text).strftime("%y%m%d")


def format_certificate_no(cert_date_yymmdd: str, cert_daily_seq: int) -> str:
    return f"ZYXMZ{cert_date_yymmdd}-{cert_daily_seq}"


def parse_certificate_no(certificate_no: str) -> tuple[str, int] | None:
    match = _DALIAN_CERT_PATTERN.match(str(certificate_no or "").strip())
    if not match:
        return None
    return match.group(1), int(match.group(2))


def _next_daily_seq(cursor, cert_date_yymmdd: str) -> int:
    cursor.execute(
        """
        SELECT next_seq
        FROM dalian_certificate_daily_counters
        WHERE cert_date_yymmdd = %s
        FOR UPDATE
        """,
        (cert_date_yymmdd,),
    )
    row = cursor.fetchone()
    if not row:
        cursor.execute(
            """
            INSERT INTO dalian_certificate_daily_counters (cert_date_yymmdd, next_seq)
            VALUES (%s, 2)
            """,
            (cert_date_yymmdd,),
        )
        return 1
    seq = int(row["next_seq"])
    cursor.execute(
        """
        UPDATE dalian_certificate_daily_counters
        SET next_seq = %s
        WHERE cert_date_yymmdd = %s
        """,
        (seq + 1, cert_date_yymmdd),
    )
    return seq


def _allocation_row_to_result(row: dict, *, is_reused: bool, is_regenerated: bool = False) -> dict[str, Any]:
    detail_id = row.get("order_detail_id")
    return {
        "certificate_no": row["certificate_no"],
        "cert_date_yymmdd": row["cert_date_yymmdd"],
        "cert_daily_seq": int(row["cert_daily_seq"]),
        "item_no_key": int(row["item_no_key"]),
        "order_detail_id": int(detail_id) if detail_id is not None else None,
        "is_reused": is_reused,
        "is_regenerated": is_regenerated,
        "has_date_conflict": False,
        "previous_certificate_no": row["certificate_no"] if is_regenerated else None,
    }


def _find_existing_allocation(cursor, order_no: str, material_no: str, item_no_key: int, order_detail_id: int | None):
    if order_detail_id is not None:
        cursor.execute(
            """
            SELECT id, order_detail_id, order_no, material_no, item_no_key, certificate_no,
                   cert_date_yymmdd, cert_daily_seq
            FROM dalian_certificate_allocations
            WHERE order_detail_id = %s
            FOR UPDATE
            """,
            (int(order_detail_id),),
        )
        existing = cursor.fetchone()
        if existing:
            return existing
    cursor.execute(
        """
        SELECT id, order_detail_id, order_no, material_no, item_no_key, certificate_no,
               cert_date_yymmdd, cert_daily_seq
        FROM dalian_certificate_allocations
        WHERE order_no = %s AND material_no = %s AND item_no_key = %s
          AND (order_detail_id IS NULL OR order_detail_id = %s)
        FOR UPDATE
        """,
        (order_no, material_no, item_no_key, order_detail_id),
    )
    return cursor.fetchone()


def allocate_dalian_certificate(
    order_no: str,
    material_no: str,
    item_no: Any,
    date_text: str,
    *,
    order_detail_id: int | None = None,
    force_regenerate: bool = False,
) -> dict[str, Any]:
    """在事务中分配或复用大连证书编号（按 order_detail_id 区分）。"""
    item_no_key = normalize_item_no_key(item_no)
    detail_id = parse_optional_order_detail_id(order_detail_id)
    requested_yymmdd = yymmdd_from_date_text(date_text)

    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)
        existing = _find_existing_allocation(cursor, order_no, material_no, item_no_key, detail_id)

        if existing and not force_regenerate:
            # 旧行缺少 order_detail_id 时，补写以便后续按明细区分
            if detail_id is not None and existing.get("order_detail_id") is None:
                cursor.execute(
                    """
                    UPDATE dalian_certificate_allocations
                    SET order_detail_id = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (detail_id, existing["id"]),
                )
                existing["order_detail_id"] = detail_id
            result = _allocation_row_to_result(existing, is_reused=True)
            result["has_date_conflict"] = existing["cert_date_yymmdd"] != requested_yymmdd
            result["allocated_certificate_no"] = existing["certificate_no"]
            result["allocated_cert_date_yymmdd"] = existing["cert_date_yymmdd"]
            cursor.close()
            return result

        daily_seq = _next_daily_seq(cursor, requested_yymmdd)
        certificate_no = format_certificate_no(requested_yymmdd, daily_seq)

        if existing and force_regenerate:
            previous_no = existing["certificate_no"]
            cursor.execute(
                """
                UPDATE dalian_certificate_allocations
                SET order_detail_id = COALESCE(%s, order_detail_id),
                    certificate_no = %s,
                    cert_date_yymmdd = %s,
                    cert_daily_seq = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (detail_id, certificate_no, requested_yymmdd, daily_seq, existing["id"]),
            )
            cursor.close()
            return {
                "certificate_no": certificate_no,
                "cert_date_yymmdd": requested_yymmdd,
                "cert_daily_seq": daily_seq,
                "item_no_key": item_no_key,
                "order_detail_id": detail_id if detail_id is not None else existing.get("order_detail_id"),
                "is_reused": False,
                "is_regenerated": True,
                "has_date_conflict": False,
                "previous_certificate_no": previous_no,
                "allocated_certificate_no": certificate_no,
                "allocated_cert_date_yymmdd": requested_yymmdd,
            }

        cursor.execute(
            """
            INSERT INTO dalian_certificate_allocations (
                order_detail_id, order_no, material_no, item_no_key, certificate_no,
                cert_date_yymmdd, cert_daily_seq
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (detail_id, order_no, material_no, item_no_key, certificate_no, requested_yymmdd, daily_seq),
        )
        cursor.close()
        return {
            "certificate_no": certificate_no,
            "cert_date_yymmdd": requested_yymmdd,
            "cert_daily_seq": daily_seq,
            "item_no_key": item_no_key,
            "order_detail_id": detail_id,
            "is_reused": False,
            "is_regenerated": False,
            "has_date_conflict": False,
            "previous_certificate_no": None,
            "allocated_certificate_no": certificate_no,
            "allocated_cert_date_yymmdd": requested_yymmdd,
        }


def update_dalian_certificate_allocation(
    order_no: str,
    material_no: str,
    item_no: Any,
    certificate_no: str,
    date_text: str,
    *,
    order_detail_id: int | None = None,
) -> dict[str, Any]:
    """手动更新已分配的大连证书编号（须与出厂日期 YYMMDD 一致）。"""
    parsed = parse_certificate_no(certificate_no)
    if not parsed:
        raise ValueError("证书编号格式无效，应为 ZYXMZYYMMDD-N")
    cert_date_yymmdd, cert_daily_seq = parsed
    requested_yymmdd = yymmdd_from_date_text(date_text)
    if cert_date_yymmdd != requested_yymmdd:
        raise ValueError("证书编号中的日期与出厂日期不一致")
    item_no_key = normalize_item_no_key(item_no)
    detail_id = parse_optional_order_detail_id(order_detail_id)
    normalized_no = format_certificate_no(cert_date_yymmdd, cert_daily_seq)

    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)
        existing = _find_existing_allocation(cursor, order_no, material_no, item_no_key, detail_id)
        if not existing:
            cursor.execute(
                """
                INSERT INTO dalian_certificate_allocations (
                    order_detail_id, order_no, material_no, item_no_key, certificate_no,
                    cert_date_yymmdd, cert_daily_seq
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (detail_id, order_no, material_no, item_no_key, normalized_no, cert_date_yymmdd, cert_daily_seq),
            )
        else:
            cursor.execute(
                """
                UPDATE dalian_certificate_allocations
                SET order_detail_id = COALESCE(%s, order_detail_id),
                    certificate_no = %s,
                    cert_date_yymmdd = %s,
                    cert_daily_seq = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (detail_id, normalized_no, cert_date_yymmdd, cert_daily_seq, existing["id"]),
            )
        cursor.close()

    return {
        "certificate_no": normalized_no,
        "cert_date_yymmdd": cert_date_yymmdd,
        "cert_daily_seq": cert_daily_seq,
        "item_no_key": item_no_key,
        "order_detail_id": detail_id,
        "is_reused": False,
        "is_regenerated": False,
        "has_date_conflict": False,
        "allocated_certificate_no": normalized_no,
        "allocated_cert_date_yymmdd": cert_date_yymmdd,
    }
