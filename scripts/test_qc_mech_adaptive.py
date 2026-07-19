"""8 套质保书 PDF 机械/炉号字段长文本溢出对比测试（AcroForm 填表 + 缩字）。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.pdf_form_renderer import extract_pdf_form_field_names, fill_pdf_form

from qc_test_common import (
    CERT_CONFIGS,
    build_full_record,
    build_render_mapping,
    get_templates_root,
    resolve_qc_pdf_template,
    write_report_json,
    write_report_txt,
)

OUT_DIR = BACKEND / "storage" / "_qc_mech_adaptive"
PDF_NAME = "quality_certificate.pdf"

LONG_TEXT_KEYS = frozenset({
    "heat_no",
    "mech_part_no_1",
    "mech_spec_1",
    "mech_part_no_2",
    "mech_spec_2",
})
LONG_VALUES: dict[str, str] = {
    "heat_no": "HEAT-2026-超长炉号-ABCDEF-1234567890",
    "mech_part_no_1": "PART-NO-VERY-LONG-IDENTIFIER-2026-001",
    "mech_spec_1": "DN600×Sch160×L=4500mm(R=1.5D)",
    "mech_part_no_2": "PART-NO-VERY-LONG-IDENTIFIER-2026-002",
    "mech_spec_2": "DN800×Sch80×L=5200mm(R=2.0D)",
}


def _apply_long_text(mapping: dict[str, str], pdf_keys: set[str]) -> dict[str, str]:
    out = dict(mapping)
    for key in LONG_TEXT_KEYS:
        if key in pdf_keys:
            out[key] = LONG_VALUES[key]
    return out


def main() -> int:
    results: list[dict] = []
    summary_lines = ["# 机械/炉号字段 PDF 长文本 AcroForm 填表对比", ""]
    all_passed = True

    for idx, (region_dir, cert, region_api, material, use_utmtpt) in enumerate(CERT_CONFIGS, start=1):
        pdf_tpl = resolve_qc_pdf_template(region_dir, cert)
        label = f"{region_dir}/{cert}"
        out_pdf = OUT_DIR / f"{region_dir}_{cert}_long.pdf"
        entry: dict = {"label": label, "pdf_template": str(pdf_tpl), "output": str(out_pdf)}

        if not pdf_tpl.is_file():
            entry["status"] = "FAIL"
            entry["error"] = "PDF 模板不存在"
            all_passed = False
            results.append(entry)
            summary_lines.append(f"[FAIL] {label}: PDF 模板不存在")
            continue

        pdf_keys = extract_pdf_form_field_names(pdf_tpl)
        tested_keys = sorted(LONG_TEXT_KEYS & pdf_keys)
        entry["pdf_keys_tested"] = tested_keys
        entry["long_values"] = {k: LONG_VALUES[k] for k in tested_keys}

        try:
            record = build_full_record(region_dir, cert, region_api, material, seq=idx)
            mapping = build_render_mapping(record, region_api, cert, use_utmtpt=use_utmtpt)
            mapping = _apply_long_text(mapping, pdf_keys)

            validation = fill_pdf_form(pdf_tpl, out_pdf, mapping, flatten=False)
            passed = validation.get("passed", False) and out_pdf.exists() and out_pdf.stat().st_size > 0
            entry.update(validation)
            entry["status"] = "PASS" if passed else "FAIL"
            entry["pdf_size"] = out_pdf.stat().st_size if out_pdf.exists() else 0

            if not passed:
                all_passed = False
                summary_lines.append(
                    f"[FAIL] {label}: leftover={validation.get('leftover_placeholders')} "
                    f"missing={validation.get('missing_in_output')}"
                )
            else:
                summary_lines.append(
                    f"[PASS] {label}: tested={','.join(tested_keys) or '(none)'} "
                    f"size={entry['pdf_size']}"
                )
        except Exception as exc:
            entry["status"] = "FAIL"
            entry["error"] = str(exc)
            all_passed = False
            summary_lines.append(f"[FAIL] {label}: {exc}")

        results.append(entry)

    summary_lines.extend(["", f"Overall: {'PASS' if all_passed else 'FAIL'}"])
    write_report_txt(OUT_DIR / "report.txt", summary_lines)
    write_report_json(OUT_DIR / "report.json", results)
    try:
        print("\n".join(summary_lines))
    except UnicodeEncodeError:
        print("Overall:", "PASS" if all_passed else "FAIL")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
