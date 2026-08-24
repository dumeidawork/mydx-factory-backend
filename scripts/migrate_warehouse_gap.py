"""仓库缺口表：附件 / 现金审批轨迹 / 价格历史 / 发票月标记。

用法（在 backend 目录）:
  python scripts/migrate_warehouse_gap.py
"""
from __future__ import annotations

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.warehouse_gap import _ready, ensure_gap_tables


def migrate() -> None:
    # 允许重复执行
    import app.services.warehouse_gap as gap

    gap._ready = False
    ensure_gap_tables()
    print("warehouse gap tables ready")


if __name__ == "__main__":
    migrate()
