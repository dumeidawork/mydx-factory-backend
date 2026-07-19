"""qc_certificate_snapshots 服务层单元测试（不依赖数据库）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.qc_certificate_snapshot import (
    snapshot_business_key_from_record,
    snapshot_business_key_from_row,
)


def test_business_key_from_order_detail_row():
    row = {
        "order_no": "400123",
        "material_no": "MAT-001",
        "item_no": 2,
        "heat_no": "H123",
        "heat_treatment_batch_no": "B456",
    }
    key = snapshot_business_key_from_row(row)
    assert key["order_no"] == "400123"
    assert key["material_no"] == "MAT-001"
    assert key["item_no_key"] == 2
    assert key["heat_no"] == "H123"
    assert key["heat_treatment_batch_no"] == "B456"


def test_business_key_from_qc_record():
    record = {
        "order_no": "200123",
        "material_no": "FR-001",
        "item_no": None,
        "delivery_content": [
            {
                "item_no": 1,
                "raw_material_no": "HN-9",
                "batch_no": "BN-8",
            }
        ],
    }
    key = snapshot_business_key_from_record(record)
    assert key["order_no"] == "200123"
    assert key["item_no_key"] == 1
    assert key["heat_no"] == "HN-9"
    assert key["heat_treatment_batch_no"] == "BN-8"


def main() -> None:
    test_business_key_from_order_detail_row()
    test_business_key_from_qc_record()
    print("test_qc_snapshot: all passed")


if __name__ == "__main__":
    main()
