"""模拟大连/法国批处理界面：多条记录批量生成质保书（Word + PDF）。"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.api.v1 import workflow as wf
from app.services.in_memory_store import quality_records

from qc_test_common import (
    build_full_record,
    extract_docx_text,
    write_report_txt,
)

OUT_DIR = BACKEND / "storage" / "_qc_batch"

DALIAN_BATCH = [
    (9101, "10-DL-qc", "A105", "dalian", "ASTM A105", False),
    (9102, "10-DL-qc", "304", "dalian", "AISI 304", False),
    (9103, "10-DL-qc", "A105_UTMTPT", "dalian", "ASTM A105", True),
]

FRANCE_BATCH = [
    (9201, "11-FR-qc", "A105", "france", "ASTM A105", False),
    (9202, "11-FR-qc", "316", "france", "AISI 316", False),
    (9203, "11-FR-qc", "304", "france", "AISI 304", False),
]


def _save_response_file(resp, dest: Path) -> None:
    src = Path(resp.path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _quick_validate(docx_path: Path) -> tuple[bool, str]:
    if not docx_path.exists():
        return False, "文件不存在"
    text = extract_docx_text(docx_path)
    if "{{" in text:
        leftovers = sorted(set(__import__("re").findall(r"\{\{([^}]+)\}\}", text)))
        return False, f"残留占位符: {leftovers[:5]}"
    return True, "OK"


def _quick_validate_pdf(pdf_path: Path) -> tuple[bool, str]:
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        return False, "文件不存在或为空"
    from app.services.pdf_template_renderer import extract_pdf_placeholders

    leftover = sorted(extract_pdf_placeholders(pdf_path))
    if leftover:
        return False, f"残留占位符: {leftover[:5]}"
    return True, "PDF直填 OK"


def _run_batch(
    batch_name: str,
    items: list[tuple],
    generate_fn,
    out_subdir: str,
) -> list[dict]:
    results: list[dict] = []
    target_dir = OUT_DIR / out_subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    for record_id, region_dir, cert, region_api, material, use_utmtpt in items:
        record = build_full_record(region_dir, cert, region_api, material, seq=record_id % 100)
        record["id"] = record_id
        quality_records[record_id] = record

        entry = {
            "record_id": record_id,
            "region": batch_name,
            "cert_type": cert,
            "material_no": record["material_no"],
            "order_no": record["order_no"],
        }

        for fmt, ext in (("word", ".docx"), ("pdf", ".pdf")):
            try:
                resp = generate_fn(
                    record_id=record_id,
                    output_format=fmt,
                    use_utmtpt="1" if use_utmtpt else "0",
                )
                fname = getattr(resp, "filename", None) or f"{record_id}{ext}"
                dest = target_dir / f"{record_id}_{cert}{ext}"
                _save_response_file(resp, dest)
                ok, msg = _quick_validate(dest) if ext == ".docx" else _quick_validate_pdf(dest)
                entry[f"{fmt}_path"] = str(dest)
                entry[f"{fmt}_ok"] = ok
                entry[f"{fmt}_msg"] = msg
            except Exception as exc:
                entry[f"{fmt}_ok"] = False
                entry[f"{fmt}_msg"] = str(exc)

        word_ok = entry.get("word_ok", False)
        pdf_ok = entry.get("pdf_ok", False)
        entry["status"] = "PASS" if word_ok and pdf_ok else ("PARTIAL" if word_ok else "FAIL")
        results.append(entry)

    return results


def main() -> int:
    dalian_results = _run_batch(
        "大连",
        DALIAN_BATCH,
        wf.generate_dalian_quality_certificate,
        "dalian",
    )
    france_results = _run_batch(
        "法国",
        FRANCE_BATCH,
        wf.generate_quality_certificate,
        "france",
    )

    all_results = dalian_results + france_results
    from app.core.config import get_settings

    pdf_mode = get_settings().qc_pdf_render_mode
    lines = ["# 质保书批量生成测试报告", f"QC_PDF_RENDER_MODE={pdf_mode}", ""]
    pass_count = 0
    fail_count = 0

    for r in all_results:
        status = r.get("status", "FAIL")
        if status == "PASS":
            pass_count += 1
        else:
            fail_count += 1
        lines.append(
            f"[{status}] {r['region']} record={r['record_id']} cert={r['cert_type']} "
            f"order={r['order_no']} material={r['material_no']}"
        )
        lines.append(f"  Word: {r.get('word_path', '-')} ({r.get('word_msg', '-')})")
        lines.append(f"  PDF:  {r.get('pdf_path', '-')} ({r.get('pdf_msg', '-')})")

    lines.extend([
        "",
        f"合计: {len(all_results)} 条, 全通过 {pass_count}, 有问题 {fail_count}",
        f"输出目录: {OUT_DIR}",
    ])

    write_report_txt(OUT_DIR / "batch_report.txt", lines)
    report_text = "\n".join(lines) + f"\n\n报告: {OUT_DIR / 'batch_report.txt'}\n"
    sys.stdout.buffer.write(report_text.encode("utf-8", errors="replace"))
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
