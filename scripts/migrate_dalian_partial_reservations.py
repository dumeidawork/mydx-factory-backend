"""迁移已有大连部分预定。请先备份数据库。

默认只打印清单，不写库。确认清单后再加 --apply。

用法（backend 目录）:
  python scripts/migrate_dalian_partial_reservations.py
  python scripts/migrate_dalian_partial_reservations.py --apply
"""
from __future__ import annotations

import json
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.dalian_order_split import migrate_existing_partial_reservations


def main() -> None:
    apply = "--apply" in sys.argv
    report = migrate_existing_partial_reservations(apply=apply)
    print(json.dumps(report, ensure_ascii=False, default=str, indent=2))
    if not apply:
        print("以上为预览。写库前请先备份数据库，再执行 --apply。")


if __name__ == "__main__":
    main()
