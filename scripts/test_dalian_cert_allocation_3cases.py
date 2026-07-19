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


class InMemoryDalianCertDb:
    def __init__(self) -> None:
        self.allocations: dict[tuple[str, str, int], dict] = {}
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
            order_no, material_no, item_no_key = params
            row = self.db.allocations.get((order_no, material_no, item_no_key))
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
        if "UPDATE dalian_certificate_allocations" in q:
            cert_no, yymmdd, daily_seq, alloc_id = params
            for row in self.db.allocations.values():
                if row["id"] == alloc_id:
                    row.update(
                        {
                            "certificate_no": cert_no,
                            "cert_date_yymmdd": yymmdd,
                            "cert_daily_seq": daily_seq,
                        }
                    )
            return
        if "INSERT INTO dalian_certificate_allocations" in q:
            order_no, material_no, item_no_key, cert_no, yymmdd, daily_seq = params
            self.db._alloc_id += 1
            self.db.allocations[(order_no, material_no, item_no_key)] = {
                "id": self.db._alloc_id,
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
        r = svc.allocate_dalian_certificate(ORDER_A, MAT_A, 10, TEST_DATE)
        assert r["is_reused"] is False
        assert r["certificate_no"] == "ZYXMZ260613-1"
        return {
            "case": "测试1：同日首次分配",
            "status": "PASS",
            "scenario": "订单+物料+条目 首次生成，当日流水从 1 开始",
            "input": {
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
        r1 = svc.allocate_dalian_certificate(ORDER_A, MAT_A, 10, TEST_DATE)
        r2 = svc.allocate_dalian_certificate(ORDER_B, MAT_B, 20, TEST_DATE)
        assert r2["certificate_no"] == "ZYXMZ260613-2"
        assert r2["cert_daily_seq"] == r1["cert_daily_seq"] + 1
        return {
            "case": "测试2：同日第二键流水递增",
            "status": "PASS",
            "scenario": "同一 YYMMDD 下不同业务键，后缀依次 1、2、3…",
            "input": {
                "order_no": ORDER_B,
                "material_no": MAT_B,
                "item_no": 20,
                "date": TEST_DATE,
            },
            "output": {
                "first_key_certificate_no": r1["certificate_no"],
                "certificate_no": r2["certificate_no"],
                "cert_daily_seq": r2["cert_daily_seq"],
                "is_reused": r2["is_reused"],
            },
        }

    def case3(_mem: InMemoryDalianCertDb):
        first = svc.allocate_dalian_certificate(ORDER_A, MAT_A, 10, TEST_DATE)
        reused = svc.allocate_dalian_certificate(ORDER_A, MAT_A, 10, TEST_DATE)
        assert reused["is_reused"] is True
        assert reused["certificate_no"] == first["certificate_no"]
        regen = svc.allocate_dalian_certificate(
            ORDER_A, MAT_A, 10, TEST_DATE_2, force_regenerate=True
        )
        assert regen["is_regenerated"] is True
        assert regen["certificate_no"] == "ZYXMZ260614-1"
        assert regen["previous_certificate_no"] == first["certificate_no"]
        return {
            "case": "测试3：同键复用 + 改日期换号",
            "status": "PASS",
            "scenario": "重复生成复用原编号；确认换号后按新日期重新分配",
            "input": {
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
