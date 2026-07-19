"""PDF 质保书 AcroForm 填表：域填充、缩字、flatten 与校验。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import fitz

from app.services.pdf_layout_utils import PLACEHOLDER_RE, build_span_index, collect_page_spans, list_to_rect
from app.services.pdf_template_renderer import _pick_fontname, resolve_pdf_font

_MIN_FIT_FONT = 5.0
_LINE_HEIGHT_FACTOR = 1.12
_MAX_CELL_LINES = 3
_QUADDING_CENTER = 1


def _mapping_value(mapping: dict[str, Any], key: str) -> str:
    raw = mapping.get(key)
    if raw is None:
        raw = mapping.get(key.strip())
    if raw is None:
        return "/"
    text = str(raw).strip()
    return text if text else "/"


def _textbox_used_height(
    text: str,
    width: float,
    height: float,
    fontsize: float,
    fontname: str,
    fontfile: str | None,
) -> float | None:
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


def _fit_fontsize(value: str, rect: fitz.Rect, base_size: float) -> float:
    if not value or value == "/":
        return base_size
    fontname = _pick_fontname(value)
    fontfile = resolve_pdf_font() if fontname == "china-s" else None
    line_h = max(base_size * _LINE_HEIGHT_FACTOR, 6.5)
    max_lines = max(1, min(_MAX_CELL_LINES, int(rect.height / line_h)))
    box_height = min(rect.height, line_h * max_lines)
    current = base_size
    while current >= _MIN_FIT_FONT:
        used = _textbox_used_height(value, rect.width, box_height, current, fontname, fontfile)
        if used is not None:
            return current
        current -= 0.5
    return _MIN_FIT_FONT


def _iter_widgets(doc: fitz.Document) -> list[fitz.Widget]:
    widgets: list[fitz.Widget] = []
    for page in doc:
        widgets.extend(page.widgets() or [])
    return widgets


def extract_pdf_form_fields(pdf_path: Path) -> dict[str, list[fitz.Rect]]:
    """域名 -> 各 widget 矩形。"""
    doc = fitz.open(str(pdf_path))
    result: dict[str, list[fitz.Rect]] = {}
    try:
        for widget in _iter_widgets(doc):
            name = (widget.field_name or "").strip()
            if not name:
                continue
            result.setdefault(name, []).append(fitz.Rect(widget.rect))
    finally:
        doc.close()
    return result


def extract_pdf_form_field_names(pdf_path: Path) -> set[str]:
    return set(extract_pdf_form_fields(pdf_path).keys())


def _extract_brace_placeholders(pdf_path: Path) -> set[str]:
    doc = fitz.open(str(pdf_path))
    keys: set[str] = set()
    try:
        for page in doc:
            items = collect_page_spans(page)
            if not items:
                continue
            full, _ = build_span_index(items)
            keys.update(k.strip() for k in PLACEHOLDER_RE.findall(full))
    finally:
        doc.close()
    return keys


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


def inspect_pdf_form_template(pdf_path: Path, word_keys: set[str] | None = None) -> dict[str, Any]:
    """AcroForm 域覆盖率与 {{}} 残留检查。"""
    if not pdf_path.is_file():
        return {"exists": False, "pdf_path": str(pdf_path)}

    form_fields = extract_pdf_form_fields(pdf_path)
    form_names = set(form_fields.keys())
    widget_count = sum(len(rects) for rects in form_fields.values())
    brace_keys = _extract_brace_placeholders(pdf_path)
    reference_keys = word_keys if word_keys is not None else form_names

    missing_from_pdf = sorted(reference_keys - form_names) if word_keys else []
    extra_fields = sorted(form_names - reference_keys) if word_keys else []
    coverage = len(form_names & reference_keys) / len(reference_keys) if reference_keys else 1.0

    duplicate_widgets = {
        name: len(rects) for name, rects in form_fields.items() if len(rects) > 1
    }

    return {
        "exists": True,
        "pdf_path": str(pdf_path),
        "widget_count": widget_count,
        "form_field_count": len(form_names),
        "reference_key_count": len(reference_keys),
        "form_field_names": sorted(form_names),
        "missing_from_pdf": missing_from_pdf,
        "extra_fields": extra_fields,
        "duplicate_widgets": duplicate_widgets,
        "brace_placeholder_keys": sorted(brace_keys),
        "has_brace_placeholders": bool(brace_keys),
        "coverage_ratio": round(coverage, 4),
        "passed_gate": widget_count > 0 and coverage >= 0.95,
    }


def _apply_widget_font(widget: fitz.Widget, value: str, fontsize: float) -> None:
    widget.text_fontsize = fontsize
    widget.text_color = (0, 0, 0)
    if _pick_fontname(value) == "china-s":
        widget.text_font = "china-s"


def _set_widget_quadding(doc: fitz.Document, widget: fitz.Widget) -> None:
    try:
        doc.xref_set_key(widget.xref, "Q", str(_QUADDING_CENTER))
    except Exception:
        pass


def add_text_field_widget(
    page: fitz.Page,
    doc: fitz.Document,
    *,
    field_name: str,
    rect: fitz.Rect,
    font_size: float,
    multiline: bool = False,
) -> fitz.Widget:
    widget = fitz.Widget()
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.field_name = field_name
    widget.field_value = ""
    widget.rect = rect
    widget.text_fontsize = font_size
    widget.text_color = (0, 0, 0)
    widget.border_width = 0
    flags = 0
    if multiline:
        flags |= fitz.PDF_TX_FIELD_IS_MULTILINE
    widget.field_flags = flags
    page.add_widget(widget)
    for created in page.widgets() or []:
        if created.field_name == field_name and fitz.Rect(created.rect) == rect:
            _set_widget_quadding(doc, created)
            return created
    widgets = list(page.widgets() or [])
    if widgets:
        last = widgets[-1]
        _set_widget_quadding(doc, last)
        return last
    return widget


def fill_pdf_form(
    template_path: Path,
    output_path: Path,
    mapping: dict[str, Any],
    *,
    flatten: bool = True,
) -> dict[str, Any]:
    """读取 AcroForm PDF 模板，填充域并写出。"""
    if not template_path.is_file():
        raise FileNotFoundError(f"PDF 模板不存在: {template_path}")

    doc = fitz.open(str(template_path))
    filled_names: set[str] = set()
    try:
        widgets = _iter_widgets(doc)
        if not widgets:
            raise ValueError("PDF 模板缺少 AcroForm 表单域")

        by_name: dict[str, list[fitz.Widget]] = {}
        for widget in widgets:
            name = (widget.field_name or "").strip()
            if not name:
                continue
            by_name.setdefault(name, []).append(widget)

        for name, group in by_name.items():
            value = _mapping_value(mapping, name)
            if not value or value == "/":
                continue
            base_size = max((w.text_fontsize or 0) for w in group) or 10.0
            fontsize = _fit_fontsize(value, fitz.Rect(group[0].rect), base_size)
            for widget in group:
                widget.field_value = value
                _apply_widget_font(widget, value, fontsize)
                widget.update()
                filled_names.add(name)

        doc.need_appearances = True
        if flatten:
            doc.bake()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path), deflate=True)
    finally:
        doc.close()

    template_names = extract_pdf_form_field_names(template_path)
    validation = validate_filled_pdf(output_path, template_names, mapping, flattened=flatten)
    validation["filled_field_count"] = len(filled_names)
    return validation


def validate_filled_pdf(
    pdf_path: Path,
    template_field_names: set[str],
    mapping: dict[str, Any],
    *,
    flattened: bool = True,
) -> dict[str, Any]:
    brace_leftover = sorted(_extract_brace_placeholders(pdf_path))
    missing: list[str] = []
    checked: list[str] = []

    doc = fitz.open(str(pdf_path))
    try:
        full_text = "".join(page.get_text() for page in doc)
        remaining_widgets = _iter_widgets(doc)
        widget_values: dict[str, str] = {}
        for widget in remaining_widgets:
            name = (widget.field_name or "").strip()
            if name:
                widget_values[name] = str(widget.field_value or "").strip()
    finally:
        doc.close()

    for key in sorted(template_field_names):
        value = _mapping_value(mapping, key)
        if not value or value == "/":
            continue
        checked.append(key)
        if flattened:
            if not _value_present_in_text(value, full_text):
                missing.append(f"{key}={value}")
        else:
            actual = widget_values.get(key, "")
            if actual != value:
                missing.append(f"{key}={value} (got {actual!r})")

    return {
        "leftover_placeholders": brace_leftover,
        "missing_in_output": missing,
        "checked_keys": checked,
        "flattened": flattened,
        "remaining_widget_count": len(remaining_widgets) if flattened else len(remaining_widgets),
        "passed": not brace_leftover and not missing,
    }
