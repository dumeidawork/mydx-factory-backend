"""大连证书编号分配 — 3 场景测试（内存模拟 DB，输出 3 条结果）。"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services import dalian_certificate_allocation as svc

TEST_DATE = "2026-6-13"
TEST_DATE_2 = "2026-6-14"
ORDER_A = "4511727401"
ORDER_B = "4511727402"
MAT_A = "FDK-130-618"
MAT_B = "FDK-130-619"
DETAIL_A = 101
DETAIL_B = 202
DETAIL_A2 = 103  # 同物料另一明细行


class InMemoryDalianCertDb:
    def __init__(self) -> None:
        self.allocations: dict[int, dict] = {}
        self.counters: dict[str, int] = {}
        self._alloc_id = 0

    def cursor(self, dictionary=True):
        return InMemoryCursor(self, dictionary=dictionary)


class InMemoryCursor:
    def __init__(self, db: InMemoryDalianCertDb, dictionary: bool = True) -> None:
        self.db = db
        self.dictionary = dictionary
        self._last_result: list[dict] | None = None

    def execute(self, query: str, params: tuple = ()) -> None:
        q = " ".join(query.split())
        if "FROM dalian_certificate_allocations" in q and "FOR UPDATE" in q:
            if "WHERE order_detail_id = %s" in q and "order_no" not in q.split("WHERE", 1)[-1]:
                detail_id = params[0]
                row = next(
                    (r for r in self.db.allocations.values() if r.get("order_detail_id") == detail_id),
                    None,
                )
                self._last_result = [dict(row)] if row else []
                return
            if "order_no = %s AND material_no = %s AND item_no_key = %s" in q:
                order_no, material_no, item_no_key, detail_id = params
                row = None
                for candidate in self.db.allocations.values():
                    if (
                        candidate["order_no"] == order_no
                        and candidate["material_no"] == material_no
                        and candidate["item_no_key"] == item_no_key
                        and (candidate.get("order_detail_id") is None or candidate.get("order_detail_id") == detail_id)
                    ):
                        row = candidate
                        break
                self._last_result = [dict(row)] if row else []
                return
        if "FROM dalian_certificate_daily_counters" in q and "FOR UPDATE" in q:
            yymmdd = params[0]
            seq = self.db.counters.get(yymmdd)
            self._last_result = [{"next_seq": seq}] if seq is not None else []
            return
        if "INSERT INTO dalian_certificate_daily_counters" in q:
            yymmdd = params[0]
            self.db.counters[yymmdd] = 2
            return
        if "UPDATE dalian_certificate_daily_counters" in q:
            next_seq, yymmdd = params
            self.db.counters[yymmdd] = int(next_seq)
            return
        if "UPDATE dalian_certificate_allocations" in q and "SET order_detail_id = %s" in q and "certificate_no" not in q:
            detail_id, alloc_id = params
            row = self.db.allocations.get(alloc_id)
            if row:
                row["order_detail_id"] = detail_id
            return
        if "UPDATE dalian_certificate_allocations" in q:
            # force regenerate / manual update
            if len(params) == 5:
                detail_id, cert_no, yymmdd, daily_seq, alloc_id = params
                row = self.db.allocations.get(alloc_id)
                if row:
                    if detail_id is not None:
                        row["order_detail_id"] = detail_id
                    row.update(
                        {
                            "certificate_no": cert_no,
                            "cert_date_yymmdd": yymmdd,
                            "cert_daily_seq": daily_seq,
                        }
                    )
            else:
                cert_no, yymmdd, daily_seq, alloc_id = params
                row = self.db.allocations.get(alloc_id)
                if row:
                    row.update(
                        {
                            "certificate_no": cert_no,
                            "cert_date_yymmdd": yymmdd,
                            "cert_daily_seq": daily_seq,
                        }
                    )
            return
        if "INSERT INTO dalian_certificate_allocations" in q:
            detail_id, order_no, material_no, item_no_key, cert_no, yymmdd, daily_seq = params
            self.db._alloc_id += 1
            self.db.allocations[self.db._alloc_id] = {
                "id": self.db._alloc_id,
                "order_detail_id": detail_id,
                "order_no": order_no,
                "material_no": material_no,
                "item_no_key": item_no_key,
                "certificate_no": cert_no,
                "cert_date_yymmdd": yymmdd,
                "cert_daily_seq": daily_seq,
            }
            return
        raise NotImplementedError(q)

    def fetchone(self):
        if not self._last_result:
            return None
        return self._last_result[0]

    def close(self) -> None:
        return None


def _run_with_memory_db(fn):
    mem = InMemoryDalianCertDb()

    @contextmanager
    def fake_get_db():
        yield mem

    with patch.object(svc, "get_db", fake_get_db):
        return fn(mem)


def run_three_cases() -> list[dict]:
    results: list[dict] = []

    def case1(_mem: InMemoryDalianCertDb):
        r = svc.allocate_dalian_certificate(ORDER_A, MAT_A, 10, TEST_DATE, order_detail_id=DETAIL_A)
        assert r["is_reused"] is False
        assert r["certificate_no"] == "ZYXMZ260613-1"
        return {
            "case": "测试1：同日首次分配",
            "status": "PASS",
            "scenario": "order_detail_id 首次生成，当日流水从 1 开始",
            "input": {
                "order_detail_id": DETAIL_A,
                "order_no": ORDER_A,
                "material_no": MAT_A,
                "item_no": 10,
                "date": TEST_DATE,
            },
            "output": {
                "certificate_no": r["certificate_no"],
                "cert_date_yymmdd": r["cert_date_yymmdd"],
                "cert_daily_seq": r["cert_daily_seq"],
                "is_reused": r["is_reused"],
            },
        }

    def case2(_mem: InMemoryDalianCertDb):
        r1 = svc.allocate_dalian_certificate(ORDER_A, MAT_A, 10, TEST_DATE, order_detail_id=DETAIL_A)
        # 同订单同物料不同明细行 → 独立编号
        r_same_mat = svc.allocate_dalian_certificate(ORDER_A, MAT_A, 10, TEST_DATE, order_detail_id=DETAIL_A2)
        r2 = svc.allocate_dalian_certificate(ORDER_B, MAT_B, 20, TEST_DATE, order_detail_id=DETAIL_B)
        assert r_same_mat["certificate_no"] == "ZYXMZ260613-2"
        assert r_same_mat["certificate_no"] != r1["certificate_no"]
        assert r2["certificate_no"] == "ZYXMZ260613-3"
        assert r2["cert_daily_seq"] == r1["cert_daily_seq"] + 2
        return {
            "case": "测试2：同物料多明细 / 同日流水递增",
            "status": "PASS",
            "scenario": "同一物料不同 order_detail_id 各拿独立编号；当日流水递增",
            "input": {
                "detail_a": DETAIL_A,
                "detail_a2": DETAIL_A2,
                "detail_b": DETAIL_B,
                "date": TEST_DATE,
            },
            "output": {
                "first_certificate_no": r1["certificate_no"],
                "same_material_other_detail_no": r_same_mat["certificate_no"],
                "other_order_certificate_no": r2["certificate_no"],
                "cert_daily_seq": r2["cert_daily_seq"],
            },
        }

    def case3(_mem: InMemoryDalianCertDb):
        first = svc.allocate_dalian_certificate(ORDER_A, MAT_A, 10, TEST_DATE, order_detail_id=DETAIL_A)
        reused = svc.allocate_dalian_certificate(ORDER_A, MAT_A, 10, TEST_DATE, order_detail_id=DETAIL_A)
        assert reused["is_reused"] is True
        assert reused["certificate_no"] == first["certificate_no"]
        regen = svc.allocate_dalian_certificate(
            ORDER_A, MAT_A, 10, TEST_DATE_2, order_detail_id=DETAIL_A, force_regenerate=True
        )
        assert regen["is_regenerated"] is True
        assert regen["certificate_no"] == "ZYXMZ260614-1"
        assert regen["previous_certificate_no"] == first["certificate_no"]
        return {
            "case": "测试3：同明细复用 + 改日期换号",
            "status": "PASS",
            "scenario": "同一 order_detail_id 重复生成复用原编号；确认换号后按新日期重新分配",
            "input": {
                "order_detail_id": DETAIL_A,
                "order_no": ORDER_A,
                "material_no": MAT_A,
                "item_no": 10,
                "first_date": TEST_DATE,
                "regenerate_date": TEST_DATE_2,
                "force_regenerate": True,
            },
            "output": {
                "first_certificate_no": first["certificate_no"],
                "reused_certificate_no": reused["certificate_no"],
                "reused_is_reused": reused["is_reused"],
                "regenerated_certificate_no": regen["certificate_no"],
                "previous_certificate_no": regen["previous_certificate_no"],
            },
        }

    for fn in (case1, case2, case3):
        try:
            results.append(_run_with_memory_db(fn))
        except Exception as exc:
            results.append({"case": fn.__name__, "status": "FAIL", "error": str(exc)})

    return results


def main() -> None:
    from test_dalian_cert_allocation import main as unit_main

    unit_main()
    print()

    results = run_three_cases()
    for idx, item in enumerate(results, 1):
        print(f"========== 测试结果 {idx} ==========")
        print(json.dumps(item, ensure_ascii=False, indent=2))
        print()

    if any(r.get("status") == "FAIL" for r in results):
        sys.exit(1)
    print("3/3 场景测试全部通过")


if __name__ == "__main__":
    main()
