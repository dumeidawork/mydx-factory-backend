"""法国自动装箱：物料-数量/托预置对照。优先读已抽出常量。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

SeedRow = Tuple[str, int, str, Optional[int], Optional[int], Optional[int], str]


def write_precomputed(rows: tuple[SeedRow, ...]) -> Path:
    dest = Path(__file__).resolve().parent / "france_auto_packing_qty_seed_data.py"
    lines = [
        '"""已核对的物料-数量/托预置对照（由装箱汇总表抽出）。"""',
        "",
        "PRECOMPUTED_MAPS = (",
    ]
    for material_no, qty, pallet_type, length_mm, width_mm, height_mm, remark in rows:
        lines.append(
            f"    ({material_no!r}, {qty}, {pallet_type!r}, {length_mm!r}, {width_mm!r}, {height_mm!r}, {remark!r}),"
        )
    lines.append(")")
    lines.append("")
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


def load_default_maps() -> tuple[SeedRow, ...]:
    try:
        from app.data.france_auto_packing_qty_seed_data import PRECOMPUTED_MAPS

        if PRECOMPUTED_MAPS:
            return tuple(PRECOMPUTED_MAPS)
    except ImportError:
        pass
    return ()
