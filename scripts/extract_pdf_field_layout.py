"""从含 {{}} 占位符的 8 套质保书 PDF 导出 AcroForm 域坐标 JSON。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.pdf_layout_utils import extract_pdf_placeholder_layout

from qc_test_common import CERT_CONFIGS, get_templates_root

OUT_DIR = BACKEND / "resources" / "qc_pdf_field_layout"
PDF_NAME = "quality_certificate.pdf"


def _layout_filename(region_dir: str, cert: str) -> str:
    region = region_dir.replace("-", "_")
    return f"{region}_{cert}.json"


def main() -> int:
    templates_root = get_templates_root()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# PDF 域坐标导出", f"输出目录: {OUT_DIR}", ""]
    ok = 0

    for region_dir, cert, *_ in CERT_CONFIGS:
        label = f"{region_dir}/{cert}"
        pdf_path = templates_root / region_dir / "pdf" / cert / PDF_NAME
        out_path = OUT_DIR / _layout_filename(region_dir, cert)
        if not pdf_path.is_file():
            lines.append(f"[FAIL] {label}: PDF 不存在 ({pdf_path})")
            continue

        layout = extract_pdf_placeholder_layout(pdf_path)
        payload = {
            "label": label,
            "source_pdf": str(pdf_path),
            "page_count": layout["page_count"],
            "field_count": layout["field_count"],
            "unique_names": layout["unique_names"],
            "fields": layout["fields"],
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        ok += 1
        lines.append(
            f"[OK] {label}: {layout['field_count']} 域, "
            f"{len(layout['unique_names'])} 唯一键 -> {out_path.name}"
        )

    lines.extend(["", f"完成: {ok}/{len(CERT_CONFIGS)}"])
    report = "\n".join(lines) + "\n"
    sys.stdout.buffer.write(report.encode("utf-8", errors="replace"))
    return 0 if ok == len(CERT_CONFIGS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
