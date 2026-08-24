"""大连证书编号分配 — 3 场景集成测试（需 MySQL）。"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.database import execute, fetch_one, get_db
from app.services.dalian_certificate_allocation import allocate_dalian_certificate


TEST_DATE = "2026-6-13"
TEST_DATE_2 = "2026-6-14"
RUN_ID = uuid.uuid4().hex[:8]
ORDER_A = f"TEST-DL-{RUN_ID}-A"
ORDER_B = f"TEST-DL-{RUN_ID}-B"
MAT_A = f"MAT-{RUN_ID}-001"
MAT_B = f"MAT-{RUN_ID}-002"


def _cleanup() -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        for order_no in (ORDER_A, ORDER_B):
            cursor.execute(
                "DELETE FROM dalian_certificate_allocations WHERE order_no = %s",
                (order_no,),
            )
        cursor.close()


def _ensure_tables() -> None:
    from apply_db_updates import main as apply_updates

    apply_updates()


def run_case(name: str, fn) -> dict:
    try:
        result = fn()
        return {"case": name, "status": "PASS", **result}
    except Exception as exc:
        return {"case": name, "status": "FAIL", "error": str(exc)}


def main() -> None:
    print("=== Dalian cert allocation: 3 integration tests ===\n")
    try:
        _ensure_tables()
    except Exception as exc:
        print(json.dumps({"error": f"数据库迁移失败: {exc}"}, ensure_ascii=False, indent=2))
        sys.exit(1)

    _cleanup()
    results: list[dict] = []

    def case1_first_alloc_same_day_seq1():
        r1 = allocate_dalian_certificate(ORDER_A, MAT_A, 10, TEST_DATE, order_detail_id=1001)
        assert r1["is_reused"] is False
        assert r1["certificate_no"].startswith("ZYXMZ260613-")
        return {
            "scenario": "同日首次分配（按 order_detail_id）",
            "input": {
                "order_detail_id": 1001,
                "order_no": ORDER_A,
                "material_no": MAT_A,
                "item_no": 10,
                "date": TEST_DATE,
            },
            "certificate_no": r1["certificate_no"],
            "cert_daily_seq": r1["cert_daily_seq"],
            "is_reused": r1["is_reused"],
        }

    def case2_second_key_same_day_increments():
        r1 = allocate_dalian_certificate(ORDER_A, MAT_A, 10, TEST_DATE, order_detail_id=1001)
        r_same = allocate_dalian_certificate(ORDER_A, MAT_A, 10, TEST_DATE, order_detail_id=1002)
        r2 = allocate_dalian_certificate(ORDER_B, MAT_B, 20, TEST_DATE, order_detail_id=2001)
        assert r_same["is_reused"] is False
        assert r_same["certificate_no"] != r1["certificate_no"]
        assert r2["is_reused"] is False
        assert r2["cert_daily_seq"] == r1["cert_daily_seq"] + 2
        assert r2["certificate_no"].startswith("ZYXMZ260613-")
        return {
            "scenario": "同物料不同明细独立编号 + 同日流水递增",
            "input": {
                "detail_1": 1001,
                "detail_2_same_material": 1002,
                "detail_other_order": 2001,
                "date": TEST_DATE,
            },
            "certificate_no": r2["certificate_no"],
            "same_material_other_detail_no": r_same["certificate_no"],
            "cert_daily_seq": r2["cert_daily_seq"],
            "previous_key_seq": r1["cert_daily_seq"],
            "is_reused": r2["is_reused"],
        }

    def case3_reuse_and_force_regenerate():
        first = allocate_dalian_certificate(ORDER_A, MAT_A, 10, TEST_DATE, order_detail_id=1001)
        reused = allocate_dalian_certificate(ORDER_A, MAT_A, 10, TEST_DATE, order_detail_id=1001)
        assert reused["is_reused"] is True
        assert reused["certificate_no"] == first["certificate_no"]
        regenerated = allocate_dalian_certificate(
            ORDER_A, MAT_A, 10, TEST_DATE_2, order_detail_id=1001, force_regenerate=True
        )
        assert regenerated["is_regenerated"] is True
        assert regenerated["certificate_no"] != first["certificate_no"]
        assert regenerated["cert_date_yymmdd"] == "260614"
        assert regenerated["previous_certificate_no"] == first["certificate_no"]
        return {
            "scenario": "同 order_detail_id 复用 + 改日期强制换号",
            "first_certificate_no": first["certificate_no"],
            "reused_certificate_no": reused["certificate_no"],
            "reused_is_reused": reused["is_reused"],
            "regenerated_certificate_no": regenerated["certificate_no"],
            "regenerated_date_yymmdd": regenerated["cert_date_yymmdd"],
            "previous_certificate_no": regenerated["previous_certificate_no"],
        }

    # Run cases sequentially; case2/3 depend on counter state — use isolated sub-cleanups
    _cleanup()
    results.append(run_case("测试1：同日首次分配", case1_first_alloc_same_day_seq1))

    _cleanup()
    # seed counter: case1 alloc then case2
    results.append(run_case("测试2：同日流水递增", case2_second_key_same_day_increments))

    _cleanup()
    results.append(run_case("测试3：复用与换号", case3_reuse_and_force_regenerate))

    _cleanup()

    for idx, item in enumerate(results, 1):
        print(f"--- Result {idx}: {item.get('case', '')} [{item.get('status')}] ---")
        print(json.dumps({k: v for k, v in item.items() if k not in ("case", "status")}, ensure_ascii=False, indent=2))
        print()

    failed = [r for r in results if r.get("status") == "FAIL"]
    if failed:
        print(f"FAILED: {len(failed)}/3")
        sys.exit(1)
    print("ALL 3 TESTS PASSED")


if __name__ == "__main__":
    main()
