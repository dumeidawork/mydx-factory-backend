"""仓库体验优化建表。用法（backend 目录）: python scripts/migrate_warehouse_ux.py"""
from __future__ import annotations

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.warehouse_ux import _UX_READY, ensure_ux_schema


def migrate() -> None:
    import app.services.warehouse_ux as ux

    ux._UX_READY = False
    ensure_ux_schema()
    print("warehouse ux schema ready")


if __name__ == "__main__":
    migrate()
