"""按 layout JSON 批量注入 AcroForm Text Field，生成质保书 PDF 模板。"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import fitz

from app.services.pdf_form_renderer import add_text_field_widget
from app.services.pdf_layout_utils import (
    build_span_index,
    collect_page_spans,
    find_placeholder_matches,
    list_to_rect,
    span_bboxes_for_range,
)

from qc_test_common import CERT_CONFIGS, get_templates_root

LAYOUT_DIR = BACKEND / "resources" / "qc_pdf_field_layout"
PREVIEW_DIR = BACKEND / "storage" / "_acroform_build"
PDF_NAME = "quality_certificate.pdf"


def _layout_filename(region_dir: str, cert: str) -> str:
    return f"{region_dir.replace('-', '_')}_{cert}.json"


def _strip_all_widgets(doc: fitz.Document) -> int:
    removed = 0
    for page in doc:
        widgets = list(page.widgets() or [])
        for widget in widgets:
            page.delete_widget(widget)
            removed += 1
    return removed


def _resolve_build_source(source_pdf: Path) -> Path:
    backup = source_pdf.with_suffix(".pdf.bak")
    if backup.is_file():
        return backup
    return source_pdf


def _redact_placeholders(doc: fitz.Document) -> int:
    removed = 0
    for page in doc:
        items = collect_page_spans(page)
        if not items:
            continue
        full, ranges = build_span_index(items)
        matches = find_placeholder_matches(full)
        if not matches:
            continue
        for match in matches:
            for bbox in span_bboxes_for_range(items, ranges, match.start(), match.end()):
                page.add_redact_annot(bbox, fill=(1, 1, 1))
            removed += 1
        if matches:
            page.apply_redactions()
    return removed


def _overlay_preview(page: fitz.Page, rect: fitz.Rect, label: str) -> None:
    page.draw_rect(rect, color=(1, 0, 0), width=0.4)
    page.insert_text((rect.x0, max(0, rect.y0 - 2)), label, fontsize=5, color=(1, 0, 0))


def build_one(
    *,
    source_pdf: Path,
    layout_path: Path,
    output_pdf: Path,
    dry_run: bool = False,
) -> dict:
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    fields = layout.get("fields") or []
    doc = fitz.open(str(source_pdf))
    try:
        stripped = _strip_all_widgets(doc)
        removed = _redact_placeholders(doc)
        added = 0
        for spec in fields:
            page_index = int(spec.get("page", 0))
            if page_index >= doc.page_count:
                continue
            page = doc[page_index]
            rect = list_to_rect(spec["rect"])
            name = str(spec["name"])
            font_size = float(spec.get("font_size") or 10)
            multiline = bool(spec.get("multiline"))
            if dry_run:
                _overlay_preview(page, rect, name)
                added += 1
                continue
            add_text_field_widget(
                page,
                doc,
                field_name=name,
                rect=rect,
                font_size=font_size,
                multiline=multiline,
            )
            added += 1

        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_pdf), deflate=True)
    finally:
        doc.close()

    return {
        "source_pdf": str(source_pdf),
        "layout_path": str(layout_path),
        "output_pdf": str(output_pdf),
        "redacted_placeholders": removed,
        "stripped_widgets": stripped,
        "widgets_added": added,
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="批量生成 AcroForm 质保书 PDF 模板")
    parser.add_argument("--dry-run", action="store_true", help="仅绘制域矩形 overlay，不写 Text Field")
    parser.add_argument("--deploy", action="store_true", help="写入 templates/.../quality_certificate.pdf")
    parser.add_argument("--label", help="仅处理指定 label，如 10-DL-qc/A105")
    args = parser.parse_args()

    templates_root = get_templates_root()
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# AcroForm 模板构建", ""]
    ok = 0

    for region_dir, cert, *_ in CERT_CONFIGS:
        label = f"{region_dir}/{cert}"
        if args.label and args.label != label:
            continue

        source_pdf = templates_root / region_dir / "pdf" / cert / PDF_NAME
        layout_path = LAYOUT_DIR / _layout_filename(region_dir, cert)
        preview_pdf = PREVIEW_DIR / f"{region_dir.replace('-', '_')}_{cert}.pdf"
        deploy_pdf = templates_root / region_dir / "pdf" / cert / PDF_NAME

        if not source_pdf.is_file():
            lines.append(f"[FAIL] {label}: 源 PDF 不存在")
            continue
        if not layout_path.is_file():
            lines.append(f"[FAIL] {label}: layout JSON 不存在 ({layout_path})")
            continue

        # 优先从 .pdf.bak（原始 {{}} 模板）构建，并清理已有 widget 避免重复注入
        build_source = _resolve_build_source(source_pdf)

        result = build_one(
            source_pdf=build_source,
            layout_path=layout_path,
            output_pdf=preview_pdf,
            dry_run=args.dry_run,
        )

        if args.deploy and not args.dry_run:
            backup = source_pdf.with_suffix(".pdf.bak")
            if not backup.is_file() and build_source.resolve() != backup.resolve():
                shutil.copy2(build_source, backup)
            try:
                shutil.copy2(preview_pdf, deploy_pdf)
            except PermissionError:
                pending = deploy_pdf.with_name("quality_certificate.acroform.pdf")
                shutil.copy2(preview_pdf, pending)
                lines.append(
                    f"[WARN] {label}: 无法覆盖 {deploy_pdf.name}（文件被占用），"
                    f"已写入 {pending.name}，请关闭占用进程后重命名替换"
                )
                continue
            target_pdf = deploy_pdf
        else:
            target_pdf = preview_pdf
        ok += 1
        mode = "overlay" if args.dry_run else ("deploy" if args.deploy else "preview")
        lines.append(
            f"[OK] {label} ({mode}): strip={result.get('stripped_widgets', 0)} "
            f"redact={result['redacted_placeholders']} "
            f"widgets={result['widgets_added']} -> {target_pdf}"
        )

    lines.extend(["", f"完成: {ok} 套"])
    report = "\n".join(lines) + "\n"
    sys.stdout.buffer.write(report.encode("utf-8", errors="replace"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
