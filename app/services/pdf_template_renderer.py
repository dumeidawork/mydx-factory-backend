"""PDF 质保书模板直填：PyMuPDF 按 span 坐标替换 {{占位符}}。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import fitz

from app.core.paths import get_backend_dir
from app.services.pdf_layout_utils import (
    PLACEHOLDER_RE,
    build_span_index,
    cell_rect_for_match,
    collect_page_spans,
    find_placeholder_matches,
    size_for_range,
    span_bboxes_for_range,
)

_PLACEHOLDER_RE = PLACEHOLDER_RE
_MIN_FIT_FONT = 5.0
_CELL_PAD = 0.6
_LINE_HEIGHT_FACTOR = 1.12
_MAX_CELL_LINES = 3
_BACKEND_DIR = get_backend_dir()
_FONT_CANDIDATES = (
    _BACKEND_DIR / "resources" / "fonts" / "NotoSansSC-Regular.otf",
    _BACKEND_DIR / "resources" / "fonts" / "NotoSansSC-Regular.ttf",
    Path(r"C:\Windows\Fonts\simsun.ttc"),
    Path(r"C:\Windows\Fonts\simsunb.ttf"),
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\msyhbd.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
)


@lru_cache(maxsize=1)
def resolve_pdf_font() -> str | None:
    for path in _FONT_CANDIDATES:
        if path.is_file():
            return str(path.resolve())
    return None

def _textbox_used_height(
    text: str,
    width: float,
    height: float,
    fontsize: float,
    fontname: str,
    fontfile: str | None,
) -> float | None:
    """在临时页面试排，返回实际占用高度；放不下则返回 None。"""
    doc = fitz.open()
    try:
        page = doc.new_page(width=max(width + 10, 40), height=max(height + 10, 40))
        rect = fitz.Rect(0, 0, width, height)
        kwargs: dict[str, Any] = {
            "fontsize": fontsize,
            "fontname": fontname,
            "align": fitz.TEXT_ALIGN_CENTER,
        }
        if fontfile:
            kwargs["fontfile"] = fontfile
        rc = page.insert_textbox(rect, text, **kwargs)
        if rc < 0:
            return None
        return height - rc
    finally:
        doc.close()


def _insert_fitted_centered_text(page: fitz.Page, cell: fitz.Rect, value: str, size: float) -> None:
    """在单元格内水平/垂直居中写入，过长则缩字换行，不超出 cell。"""
    if not value or value == "/":
        return

    inner = fitz.Rect(
        cell.x0 + _CELL_PAD,
        cell.y0 + _CELL_PAD,
        cell.x1 - _CELL_PAD,
        cell.y1 - _CELL_PAD,
    )
    if inner.width < 2 or inner.height < 2:
        inner = cell

    fontname = _pick_fontname(value)
    fontfile = resolve_pdf_font() if fontname == "china-s" else None

    line_h = max(size * _LINE_HEIGHT_FACTOR, 6.5)
    max_lines = max(1, min(_MAX_CELL_LINES, int(inner.height / line_h)))
    box_height = min(inner.height, line_h * max_lines)

    chosen_size = _MIN_FIT_FONT
    chosen_used = box_height
    current = size
    while current >= _MIN_FIT_FONT:
        used = _textbox_used_height(
            value, inner.width, box_height, current, fontname, fontfile
        )
        if used is not None:
            chosen_size = current
            chosen_used = used
            break
        current -= 0.5

    vpad = max(0.0, (inner.height - chosen_used) / 2)
    write_rect = fitz.Rect(
        inner.x0,
        inner.y0 + vpad,
        inner.x1,
        inner.y0 + vpad + chosen_used,
    )
    kwargs: dict[str, Any] = {
        "fontsize": chosen_size,
        "fontname": fontname,
        "align": fitz.TEXT_ALIGN_CENTER,
    }
    if fontfile:
        kwargs["fontfile"] = fontfile
    page.insert_textbox(write_rect, value, **kwargs)


def extract_pdf_placeholders(pdf_path: Path) -> set[str]:
    doc = fitz.open(str(pdf_path))
    keys: set[str] = set()
    try:
        for page in doc:
            items = collect_page_spans(page)
            full, _ = build_span_index(items)
            keys.update(k.strip() for k in _PLACEHOLDER_RE.findall(full))
    finally:
        doc.close()
    return keys


def _mapping_value(mapping: dict[str, Any], key: str) -> str:
    raw = mapping.get(key)
    if raw is None:
        raw = mapping.get(key.strip())
    if raw is None:
        return "/"
    text = str(raw).strip()
    return text if text else "/"


def _pick_fontname(value: str) -> str:
    for ch in value:
        if ord(ch) > 127:
            return "china-s"
    return "helv"


def _find_placeholder_matches(full_text: str):
    return find_placeholder_matches(full_text)


def inspect_pdf_template(pdf_path: Path, word_keys: set[str] | None = None) -> dict[str, Any]:
    """分析 PDF 模板占位符可识别性与 search_for 命中率。"""
    if not pdf_path.is_file():
        return {"exists": False, "pdf_path": str(pdf_path)}

    pdf_keys = extract_pdf_placeholders(pdf_path)
    doc = fitz.open(str(pdf_path))
    search_ok: list[str] = []
    search_fail: list[str] = []
    reference_keys = word_keys if word_keys is not None else pdf_keys
    try:
        for key in sorted(reference_keys):
            token = f"{{{{{key}}}}}"
            hits = sum(len(page.search_for(token)) for page in doc)
            if hits:
                search_ok.append(key)
            else:
                search_fail.append(key)
    finally:
        doc.close()

    missing_from_pdf = sorted(reference_keys - pdf_keys) if word_keys else []
    coverage = len(pdf_keys & reference_keys) / len(reference_keys) if reference_keys else 1.0

    return {
        "exists": True,
        "pdf_path": str(pdf_path),
        "pdf_key_count": len(pdf_keys),
        "reference_key_count": len(reference_keys),
        "merged_span_keys": sorted(pdf_keys),
        "search_for_ok": search_ok,
        "search_for_fail": search_fail,
        "missing_from_pdf": missing_from_pdf,
        "coverage_ratio": round(coverage, 4),
        "passed_gate": coverage >= 0.95,
    }


def render_pdf_template(
    template_path: Path,
    output_path: Path,
    mapping: dict[str, Any],
) -> dict[str, Any]:
    """读取 PDF 模板，按 span 坐标替换占位符并写出。"""
    if not template_path.is_file():
        raise FileNotFoundError(f"PDF 模板不存在: {template_path}")

    doc = fitz.open(str(template_path))
    replaced = 0

    try:
        for page in doc:
            items = collect_page_spans(page)
            if not items:
                continue
            full, ranges = build_span_index(items)
            matches = find_placeholder_matches(full)
            if not matches:
                continue

            redactions: list[tuple[list[fitz.Rect], fitz.Rect, str, float]] = []
            for match in matches:
                key = match.group(1).strip()
                value = _mapping_value(mapping, key)
                span_bboxes = span_bboxes_for_range(items, ranges, match.start(), match.end())
                cell = cell_rect_for_match(items, ranges, match.start(), match.end())
                if not span_bboxes or cell is None:
                    continue
                size = size_for_range(items, ranges, match.start(), match.end())
                redactions.append((span_bboxes, cell, value, size))

            for span_bboxes, _, _, _ in redactions:
                for bbox in span_bboxes:
                    page.add_redact_annot(bbox, fill=(1, 1, 1))
            if redactions:
                page.apply_redactions()
                replaced += len(redactions)

            for _, cell, value, size in redactions:
                _insert_fitted_centered_text(page, cell, value, size)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
    finally:
        doc.close()

    template_keys = extract_pdf_placeholders(template_path)
    validation = validate_rendered_pdf(output_path, template_keys, mapping)
    validation["replaced_count"] = replaced
    return validation


def _value_present_in_text(value: str, full_text: str) -> bool:
    if not value or value == "/":
        return True
    if value in full_text:
        return True
    collapsed_page = " ".join(full_text.split())
    collapsed_value = " ".join(value.split())
    if collapsed_value in collapsed_page:
        return True
    if "/" in value:
        parts = [part.strip() for part in value.split("/") if part.strip()]
        if parts and all(part in full_text for part in parts):
            return True
    return False


def validate_rendered_pdf(
    pdf_path: Path,
    template_keys: set[str],
    mapping: dict[str, Any],
) -> dict[str, Any]:
    leftover = sorted(extract_pdf_placeholders(pdf_path))
    missing: list[str] = []
    checked: list[str] = []

    doc = fitz.open(str(pdf_path))
    try:
        full_text = "".join(page.get_text() for page in doc)
    finally:
        doc.close()

    for key in sorted(template_keys):
        value = _mapping_value(mapping, key)
        if not value or value == "/":
            continue
        checked.append(key)
        if not _value_present_in_text(value, full_text):
            missing.append(f"{key}={value}")

    return {
        "leftover_placeholders": leftover,
        "missing_in_output": missing,
        "checked_keys": checked,
        "passed": not leftover,
    }
