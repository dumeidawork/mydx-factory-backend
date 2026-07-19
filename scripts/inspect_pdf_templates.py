"""扫描 8 套 PDF 质保书模板 AcroForm 域覆盖率。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.pdf_form_renderer import inspect_pdf_form_template

from qc_test_common import CERT_CONFIGS, extract_template_placeholders, get_templates_root, write_report_json, write_report_txt

OUT_DIR = BACKEND / "storage" / "_pdf_template_inspect"
PDF_NAME = "quality_certificate.pdf"
WORD_NAME = "quality_certificate.docx"


def main() -> int:
    templates_root = get_templates_root()
    lines = ["# PDF 质保书 AcroForm 模板检查报告", f"templates_dir: {templates_root}", ""]
    results: list[dict] = []
    all_passed = True

    for region_dir, cert, *_ in CERT_CONFIGS:
        label = f"{region_dir}/{cert}"
        word_path = templates_root / region_dir / "word" / cert / WORD_NAME
        pdf_path = templates_root / region_dir / "pdf" / cert / PDF_NAME
        word_keys: set[str] = set()
        if word_path.is_file():
            word_keys = extract_template_placeholders(word_path)

        report = inspect_pdf_form_template(pdf_path, word_keys or None)
        report["label"] = label
        report["word_path"] = str(word_path)
        report["word_key_count"] = len(word_keys)
        results.append(report)

        if not report.get("exists"):
            all_passed = False
            lines.append(f"[FAIL] {label}: PDF 模板不存在 ({pdf_path})")
            continue

        if report.get("widget_count", 0) == 0:
            all_passed = False
            gate = "FAIL"
        else:
            gate = "PASS" if report["passed_gate"] else "WARN"
            if not report["passed_gate"]:
                all_passed = False

        coverage_pct = int(report["coverage_ratio"] * 100)
        lines.append(
            f"[{gate}] {label}: Word {len(word_keys)} 键, "
            f"AcroForm {report['form_field_count']} 域 / {report['widget_count']} widget, "
            f"覆盖率 {coverage_pct}%"
        )
        if report.get("has_brace_placeholders"):
            lines.append(f"  WARN: 仍检测到 {{{{}}}} 文字键 {report['brace_placeholder_keys'][:8]}")
        if report["missing_from_pdf"]:
            lines.append(f"  缺失域: {report['missing_from_pdf'][:12]}")
        if report.get("extra_fields"):
            lines.append(f"  多余域: {report['extra_fields'][:12]}")
        dup = report.get("duplicate_widgets") or {}
        if dup:
            sample = ", ".join(f"{k}×{v}" for k, v in list(dup.items())[:6])
            lines.append(f"  同名多 widget: {sample}")

    lines.extend(["", f"门禁(≥95% 覆盖率且 widget>0): {'全部通过' if all_passed else '存在未达标'}"])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_report_txt(OUT_DIR / "report.txt", lines)
    write_report_json(OUT_DIR / "report.json", results)

    report_text = "\n".join(lines) + f"\n\n报告: {OUT_DIR / 'report.txt'}\n"
    sys.stdout.buffer.write(report_text.encode("utf-8", errors="replace"))
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
