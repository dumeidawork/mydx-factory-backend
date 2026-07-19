"""将 8 套质保书 Word 模板同步转换为 PDF 模板（需本机安装 MS Word）。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from qc_test_common import CERT_CONFIGS, convert_docx_to_pdf, get_templates_root

TEMPLATE_NAME = "quality_certificate.docx"
PDF_NAME = "quality_certificate.pdf"


def main() -> int:
    lines: list[str] = ["# 质保书模板 PDF 同步", ""]
    failures: list[str] = []
    total_start = time.perf_counter()

    for region_dir, cert, *_ in CERT_CONFIGS:
        templates_root = get_templates_root()
        docx_path = templates_root / region_dir / "word" / cert / TEMPLATE_NAME
        pdf_path = templates_root / region_dir / "pdf" / cert / PDF_NAME
        label = f"{region_dir}/{cert}"

        if not docx_path.exists():
            msg = f"Word 模板不存在: {docx_path}"
            failures.append(f"{label}: {msg}")
            lines.append(f"[SKIP] {label}: {msg}")
            continue

        started = time.perf_counter()
        try:
            convert_docx_to_pdf(docx_path, pdf_path)
            elapsed = time.perf_counter() - started
            size = pdf_path.stat().st_size if pdf_path.exists() else 0
            ok = size > 0
            status = "OK" if ok else "FAIL(empty)"
            lines.append(f"[{status}] {label}: {pdf_path} ({size} bytes, {elapsed:.1f}s)")
            if not ok:
                failures.append(f"{label}: PDF 为空")
        except Exception as exc:
            elapsed = time.perf_counter() - started
            failures.append(f"{label}: {exc}")
            lines.append(f"[FAIL] {label}: {exc} ({elapsed:.1f}s)")

    total_elapsed = time.perf_counter() - total_start
    lines.extend(["", f"合计耗时: {total_elapsed:.1f}s", f"失败: {len(failures)} 套"])
    report = BACKEND / "storage" / "_qc_template_pdf_sync.txt"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report_text = "\n".join(lines) + f"\n\n报告: {report}\n"
    sys.stdout.buffer.write(report_text.encode("utf-8", errors="replace"))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
