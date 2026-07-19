"""从客户正式唛头 Excel 生成系统用表头模板（保留前 3 行表头 + 第 4 行样式参考）。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openpyxl import load_workbook

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.paths import get_templates_dir
DEFAULT_SOURCE = (
    Path.home()
    / "OneDrive"
    / "xwechat_files"
    / "wxid_94b5umlg0h1g22_e4bc"
    / "msg"
    / "file"
    / "2026-04"
    / "大连西门子唛头2026-4-1(1).xlsx"
)
MARK_TEMPLATE_DIR = get_templates_dir() / "10-DL-mark"
OUTPUTS = [
    MARK_TEMPLATE_DIR / "dalian_siemens_shipping_mark.xlsx",
]


def build_template(source: Path, dest: Path) -> None:
    wb = load_workbook(source)
    ws = wb.active
    ws.title = "Sheet1"
    if ws.max_row > 4:
        ws.delete_rows(5, ws.max_row - 4)
    for merge_range in list(ws.merged_cells.ranges):
        if merge_range.min_row >= 4:
            try:
                ws.unmerge_cells(str(merge_range))
            except KeyError:
                pass
    for col in range(1, 11):
        ws.cell(4, col).value = "=G4*F4" if col == 8 else None
    dest.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dest)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成大连西门子唛头表头模板")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="客户正式唛头 xlsx 路径")
    parser.add_argument("--output", type=Path, help="仅输出到指定路径（默认写入 templates 等多处）")
    args = parser.parse_args()
    if not args.source.exists():
        print(f"源文件不存在: {args.source}", file=sys.stderr)
        return 1
    targets = [args.output] if args.output else OUTPUTS
    for target in targets:
        build_template(args.source, target)
        print(f"已生成: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
