"""图纸-法兰类型预置数据部署。以已核对的 seed_data 为准，不再解析 Excel。

  python scripts/deploy_drawing_flange_seed.py --from-db
      把当前库对照导出为 app/data/drawing_flange_type_seed_data.py
  python scripts/deploy_drawing_flange_seed.py
      用 seed_data 清空后灌入目标库
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.api.v1 import drawing_flange_type_maps as maps  # noqa: E402
from app.core.database import execute, fetch_all, fetch_one  # noqa: E402
from app.data.drawing_flange_type_seed import write_precomputed  # noqa: E402


def _load_seed_rows() -> tuple[tuple[str, str, str], ...]:
    from app.data.drawing_flange_type_seed_data import PRECOMPUTED_MAPS

    return tuple(PRECOMPUTED_MAPS)


def _rows_from_db() -> tuple[tuple[str, str, str], ...]:
    rows = fetch_all(
        """
        SELECT drawing_no, flange_type, remark
        FROM drawing_flange_type_maps
        ORDER BY drawing_no
        """
    )
    out: list[tuple[str, str, str]] = []
    for row in rows:
        drawing_no = maps.drawing_no_base(row.get("drawing_no") or "")
        flange_type = maps.normalize_flange_type(row.get("flange_type") or "") or maps.DEFAULT_FLANGE_TYPE
        remark = maps._normalize_str(row.get("remark"))
        if not drawing_no:
            continue
        out.append((drawing_no, flange_type, remark))
    return tuple(out)


def _print_summary(rows: tuple[tuple[str, str, str], ...], title: str) -> None:
    print(title)
    print(f"条数：{len(rows)}")
    print("类型分布：", dict(Counter(item[1] for item in rows)))
    noted = sum(1 for item in rows if item[2])
    print(f"带备注：{noted} 条")
    print("前 20 条：")
    for drawing_no, flange_type, remark in rows[:20]:
        extra = f"  | {remark}" if remark else ""
        print(f"  {drawing_no} -> {flange_type}{extra}")


def export_from_db() -> Path:
    rows = _rows_from_db()
    if not rows:
        raise SystemExit("当前库没有图纸-法兰类型对照，无法导出预置数据")
    dest = write_precomputed(rows)
    _print_summary(rows, "已按当前库导出预置数据")
    print(f"写入：{dest}")
    return dest


def deploy_from_seed() -> None:
    try:
        rows = _load_seed_rows()
    except ImportError:
        raise SystemExit("未找到 drawing_flange_type_seed_data.py，请先执行 --from-db") from None
    if not rows:
        raise SystemExit("预置对照为空，请先执行 --from-db")

    _print_summary(rows, "预置数据（seed_data）")

    execute(maps.CREATE_TABLE_SQL)
    execute(maps.CREATE_LOG_TABLE_SQL)
    execute("DELETE FROM drawing_flange_type_map_logs")
    execute("DELETE FROM drawing_flange_type_maps")
    execute("ALTER TABLE drawing_flange_type_maps AUTO_INCREMENT = 1")
    execute("ALTER TABLE drawing_flange_type_map_logs AUTO_INCREMENT = 1")

    inserted = 0
    for drawing_no, flange_type, remark in rows:
        drawing_key = maps.drawing_no_base(drawing_no)
        type_key = maps.normalize_flange_type(flange_type) or maps.DEFAULT_FLANGE_TYPE
        note = maps._normalize_str(remark)
        if not drawing_key or maps._find_by_drawing(drawing_key):
            continue
        new_id = execute(
            """
            INSERT INTO drawing_flange_type_maps (
                drawing_no, flange_type, remark, created_by, updated_by
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (drawing_key, type_key, note, maps.SEED_OPERATOR, maps.SEED_OPERATOR),
        )
        maps._write_log(
            action="create",
            operator_name=maps.SEED_OPERATOR,
            row_id=new_id,
            drawing_no=drawing_key,
            change_summary={
                "op": "create",
                "fields": {"drawing_no": drawing_key, "flange_type": type_key, "remark": note},
            },
        )
        inserted += 1

    after = fetch_one("SELECT COUNT(*) AS cnt FROM drawing_flange_type_maps")
    after_count = int((after or {}).get("cnt") or 0)
    print(f"本次新增：{inserted} 条")
    print(f"入库后：{after_count} 条")
    db_types = fetch_all(
        "SELECT flange_type, COUNT(*) AS cnt FROM drawing_flange_type_maps GROUP BY flange_type ORDER BY flange_type"
    )
    print("库内类型分布：")
    for row in db_types:
        print(f"  {row.get('flange_type')}: {row.get('cnt')}")
    print("可打开【建档 / 图纸-法兰类型】查看。")


def main() -> None:
    parser = argparse.ArgumentParser(description="图纸-法兰类型预置数据：导出当前库或按 seed 部署")
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="把当前库对照导出为 seed_data，不改库",
    )
    args = parser.parse_args()
    if args.from_db:
        export_from_db()
        return
    deploy_from_seed()


if __name__ == "__main__":
    main()
