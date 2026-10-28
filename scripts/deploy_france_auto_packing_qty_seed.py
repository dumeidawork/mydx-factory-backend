"""物料-数量/托预置数据部署。以已核对的 seed_data 为准，不再解析 Excel。

  python scripts/deploy_france_auto_packing_qty_seed.py --from-db
      把当前库对照导出为 app/data/france_auto_packing_qty_seed_data.py
  python scripts/deploy_france_auto_packing_qty_seed.py --sql
      生成服务器可执行的 SQL（不清空当前库）
  python scripts/deploy_france_auto_packing_qty_seed.py
      用 seed_data 清空后灌入当前 .env 指向的库（本地/服务器均用此命令）
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.api.v1 import france_auto_packing_qty_maps as maps  # noqa: E402
from app.api.v1.pallet_archives import _ensure_tables as ensure_pallet_tables  # noqa: E402
from app.core.database import execute, fetch_all, fetch_one  # noqa: E402
from app.data.france_auto_packing_qty_seed import SeedRow, write_precomputed  # noqa: E402

SQL_PATH = ROOT / "scripts" / "deploy_france_auto_packing_qty_seed.sql"


def _load_seed_rows() -> tuple[SeedRow, ...]:
    from app.data.france_auto_packing_qty_seed_data import PRECOMPUTED_MAPS

    return tuple(PRECOMPUTED_MAPS)


def _rows_from_db() -> tuple[SeedRow, ...]:
    rows = fetch_all(
        """
        SELECT material_no, qty_per_pallet, pallet_type, length_mm, width_mm, height_mm, remark
        FROM france_auto_packing_qty_maps
        ORDER BY id
        """
    )
    out: list[SeedRow] = []
    for row in rows:
        material_no = maps._normalize_str(row.get("material_no"))
        pallet_type = maps._normalize_str(row.get("pallet_type"))
        qty = int(row.get("qty_per_pallet") or 0)
        if not material_no or not pallet_type or qty <= 0:
            continue
        out.append(
            (
                material_no,
                qty,
                pallet_type,
                row.get("length_mm"),
                row.get("width_mm"),
                row.get("height_mm"),
                maps._normalize_str(row.get("remark")),
            )
        )
    return tuple(out)


def _print_summary(rows: tuple[SeedRow, ...], title: str) -> None:
    print(title)
    print(f"条数：{len(rows)}")
    print("托盘分布：", dict(Counter(item[2] for item in rows)))
    noted = sum(1 for item in rows if item[6])
    print(f"带备注：{noted} 条")
    print("前 20 条：")
    for material_no, qty, pallet_type, length_mm, width_mm, height_mm, remark in rows[:20]:
        extra = f"  | {remark}" if remark else ""
        print(f"  {material_no} -> {qty}件/{pallet_type} {length_mm}×{width_mm}×{height_mm}{extra}")


def export_from_db() -> Path:
    rows = _rows_from_db()
    if not rows:
        raise SystemExit("当前库没有物料-数量/托对照，无法导出预置数据")
    dest = write_precomputed(rows)
    _print_summary(rows, "已按当前库导出预置数据")
    print(f"写入：{dest}")
    return dest


def _sql_str(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


def _sql_int(value: object) -> str:
    if value is None or value == "":
        return "NULL"
    return str(int(value))


def write_sql(rows: tuple[SeedRow, ...], dest: Path = SQL_PATH) -> Path:
    lines = [
        "-- 铭远ERP：法国自动装箱【物料-数量/托】预置数据（服务器可单独执行）",
        "-- 会清空 france_auto_packing_qty_maps / france_auto_packing_qty_map_logs 后全量灌入。",
        "-- 用法（库名按服务器 backend/.env 的 MYSQL_DATABASE，例如 root123）：",
        "--   mysql -h localhost -P 3306 -u <用户> -p <库名> < scripts/deploy_france_auto_packing_qty_seed.sql",
        "-- DN 在 SQL 中留空；用 Python 脚本部署时会按目标库物料表自动带出。",
        "",
        "SET NAMES utf8mb4;",
        "SET FOREIGN_KEY_CHECKS = 0;",
        maps.CREATE_TABLE_SQL.strip() + ";",
        maps.CREATE_LOG_TABLE_SQL.strip() + ";",
        "DELETE FROM france_auto_packing_qty_map_logs;",
        "DELETE FROM france_auto_packing_qty_maps;",
        "ALTER TABLE france_auto_packing_qty_maps AUTO_INCREMENT = 1;",
        "ALTER TABLE france_auto_packing_qty_map_logs AUTO_INCREMENT = 1;",
        "",
    ]
    for material_no, qty, pallet_type, length_mm, width_mm, height_mm, remark in rows:
        summary = json.dumps(
            {
                "op": "create",
                "fields": {
                    "material_no": material_no,
                    "qty_per_pallet": qty,
                    "pallet_type": pallet_type,
                    "length_mm": length_mm,
                    "width_mm": width_mm,
                    "height_mm": height_mm,
                    "remark": remark,
                },
            },
            ensure_ascii=False,
        )
        lines.append(
            "INSERT INTO france_auto_packing_qty_maps ("
            "material_no, dn_value, qty_per_pallet, pallet_type, "
            "length_mm, width_mm, height_mm, remark, created_by, updated_by"
            ") VALUES ("
            f"{_sql_str(material_no)}, '', {int(qty)}, {_sql_str(pallet_type)}, "
            f"{_sql_int(length_mm)}, {_sql_int(width_mm)}, {_sql_int(height_mm)}, "
            f"{_sql_str(remark)}, {_sql_str(maps.SEED_OPERATOR)}, {_sql_str(maps.SEED_OPERATOR)}"
            ");"
        )
        lines.append(
            "INSERT INTO france_auto_packing_qty_map_logs ("
            "row_id, material_no, action, operator_name, change_summary"
            ") VALUES ("
            "LAST_INSERT_ID(), "
            f"{_sql_str(material_no)}, 'create', {_sql_str(maps.SEED_OPERATOR)}, {_sql_str(summary)}"
            ");"
        )
    lines.extend(
        [
            "",
            "SET FOREIGN_KEY_CHECKS = 1;",
            "",
        ]
    )
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


def deploy_from_seed() -> None:
    try:
        rows = _load_seed_rows()
    except ImportError:
        raise SystemExit("未找到 france_auto_packing_qty_seed_data.py，请先执行 extract 脚本") from None
    if not rows:
        raise SystemExit("预置对照为空，请先执行 extract 脚本")

    _print_summary(rows, "预置数据（seed_data）")
    db = fetch_one("SELECT DATABASE() AS db")
    print(f"目标库：{(db or {}).get('db')}")

    ensure_pallet_tables()
    maps._ensure_tables(seed=False)
    execute("DELETE FROM france_auto_packing_qty_map_logs")
    execute("DELETE FROM france_auto_packing_qty_maps")
    execute("ALTER TABLE france_auto_packing_qty_maps AUTO_INCREMENT = 1")
    execute("ALTER TABLE france_auto_packing_qty_map_logs AUTO_INCREMENT = 1")

    inserted = 0
    for row in rows:
        if maps.insert_qty_seed_row(*row):
            inserted += 1

    after = fetch_one("SELECT COUNT(*) AS cnt FROM france_auto_packing_qty_maps")
    after_count = int((after or {}).get("cnt") or 0)
    print(f"本次新增：{inserted} 条")
    print(f"入库后：{after_count} 条")
    db_types = fetch_all(
        """
        SELECT pallet_type, COUNT(*) AS cnt
        FROM france_auto_packing_qty_maps
        GROUP BY pallet_type
        ORDER BY pallet_type
        """
    )
    print("库内托盘分布：")
    for row in db_types:
        print(f"  {row.get('pallet_type')}: {row.get('cnt')}")
    with_dn = fetch_one(
        "SELECT COUNT(*) AS cnt FROM france_auto_packing_qty_maps WHERE dn_value IS NOT NULL AND dn_value<>''"
    )
    print(f"已带出 DN：{int((with_dn or {}).get('cnt') or 0)} 条")
    print("可打开【装箱 / 法国自动装箱 / 物料-数量/托】查看。")


def main() -> None:
    parser = argparse.ArgumentParser(description="物料-数量/托预置数据：导出当前库、生成 SQL 或按 seed 部署")
    parser.add_argument("--from-db", action="store_true", help="把当前库对照导出为 seed_data，不改库")
    parser.add_argument("--sql", action="store_true", help="只生成服务器 SQL，不改库")
    args = parser.parse_args()
    if args.from_db:
        export_from_db()
        return
    if args.sql:
        try:
            rows = _load_seed_rows()
        except ImportError:
            raise SystemExit("未找到 france_auto_packing_qty_seed_data.py，请先执行 extract 脚本") from None
        dest = write_sql(rows)
        _print_summary(rows, "已生成服务器 SQL")
        print(f"写入：{dest}")
        return
    deploy_from_seed()


if __name__ == "__main__":
    main()
