"""按二维码标签模板 + 打字要求明细生成可打印 PDF。"""
from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Any

import fitz

from app.services.pdf_template_renderer import resolve_pdf_font
from app.utils.datamatrix_ecc200 import encode_datamatrix
from app.utils.qrcode_label import make_qr_png

MM_TO_PT = 72.0 / 25.4
PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")

PAPER_SIZES_MM: dict[str, tuple[float, float]] = {
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "LETTER": (215.9, 279.4),
    "LABEL": (100.0, 150.0),
}

ALIGN_MAP = {"left": 0, "center": 1, "right": 2}

WIN_FONTS = Path(r"C:\Windows\Fonts")
FONT_FILES: dict[str, tuple[str, str]] = {
    "宋体": ("simsun.ttc", "simsun.ttc"),
    "黑体": ("simhei.ttf", "simhei.ttf"),
    "微软雅黑": ("msyh.ttc", "msyhbd.ttc"),
    "楷体": ("simkai.ttf", "simkai.ttf"),
    "Calibri": ("calibri.ttf", "calibrib.ttf"),
    "Arial": ("arial.ttf", "arialbd.ttf"),
    "Times New Roman": ("times.ttf", "timesbd.ttf"),
    "Courier New": ("cour.ttf", "courbd.ttf"),
}


def paper_size_mm(settings: dict[str, Any]) -> tuple[float, float]:
    paper = str(settings.get("paper") or "A4").upper()
    orientation = str(settings.get("orientation") or "portrait")
    if paper == "CUSTOM":
        width = float(settings.get("paper_w_mm") or 210)
        height = float(settings.get("paper_h_mm") or 297)
        return width, height
    width, height = PAPER_SIZES_MM.get(paper, PAPER_SIZES_MM["A4"])
    if orientation == "landscape":
        return height, width
    return width, height


def layout_per_page(settings: dict[str, Any]) -> int:
    cols = max(1, int(settings.get("cols") or 1))
    rows = max(1, int(settings.get("rows") or 1))
    return cols * rows


def label_page_count(quantity: int, settings: dict[str, Any]) -> int:
    per_page = layout_per_page(settings)
    qty = max(0, int(quantity or 0))
    if qty <= 0:
        return 0
    return math.ceil(qty / per_page)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def fill_placeholders(content: str, values: dict[str, str]) -> str:
    return PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), ""), content or "")


def item_values(item: dict[str, Any]) -> dict[str, str]:
    qty = item.get("quantity")
    weight = item.get("unit_weight")
    item_no = item.get("item_no")
    return {
        "order_no": _as_text(item.get("order_no")),
        "factory_order_no": _as_text(item.get("factory_order_no")),
        "item_no": "" if item_no is None or item_no == "" else str(item_no),
        "drawing_rev_no": _as_text(item.get("drawing_rev_no")),
        "material_no": _as_text(item.get("material_no")),
        "part_name": _as_text(item.get("part_name")),
        "typing_content": _as_text(item.get("typing_content")),
        "spec": _as_text(item.get("spec")),
        "model_standard": _as_text(item.get("model_standard")),
        "qr_spec": _as_text(item.get("qr_spec")),
        "heat_no": _as_text(item.get("heat_no")),
        "heat_treatment_batch_no": _as_text(item.get("heat_treatment_batch_no")),
        "quantity": "" if qty is None or qty == "" else str(qty),
        "unit_weight": "" if weight is None or weight == "" else str(weight),
        "remark": _as_text(item.get("remark")),
    }


def resolve_datamatrix_payload(item: dict[str, Any], template: str = "") -> str:
    text = (template or "").strip()
    if text:
        filled = fill_placeholders(text, item_values(item))
        return filled if filled.strip() else "MINGYUAN"
    return build_datamatrix_payload(item)


def build_datamatrix_payload(item: dict[str, Any]) -> str:
    values = item_values(item)
    material = values["material_no"] or values["part_name"]
    heat = values["heat_no"]
    batch = values["heat_treatment_batch_no"]
    order = values["order_no"]
    supplier = _as_text(item.get("supplier_no")) or _as_text(os.getenv("QR_LABEL_SUPPLIER_NO"))
    parts: list[str] = []
    if material:
        parts.append(f"1P{material.replace(' ', '')}")
    if heat or batch:
        parts.append(f"1T{heat}_{batch}".replace(" ", ""))
    if supplier:
        parts.append(f"21V{supplier.replace(' ', '')}")
    if order:
        parts.append(f"K{order.replace(' ', '')}")
    return "+".join(parts) or "MINGYUAN"


def _mm(value: float) -> float:
    return float(value) * MM_TO_PT


_LATIN_FONTS = {"Calibri", "Arial", "Times New Roman", "Courier New"}


def _has_cjk(text: str) -> bool:
    return any(ord(ch) > 127 for ch in text)


def _font_file(family: str, bold: bool) -> str | None:
    pair = FONT_FILES.get(family)
    if pair and WIN_FONTS.is_dir():
        name = pair[1] if bold else pair[0]
        path = WIN_FONTS / name
        if path.is_file():
            return str(path)
        fallback = WIN_FONTS / pair[0]
        if fallback.is_file():
            return str(fallback)
    return resolve_pdf_font()


def _resolve_label_font(family: str, bold: bool, text: str) -> tuple[str, str | None]:
    use_family = family
    if _has_cjk(text) and family in _LATIN_FONTS:
        use_family = "微软雅黑"
    fontfile = _font_file(use_family, bold)
    if fontfile:
        suffix = "b" if bold else "r"
        safe = "".join(ch if ch.isascii() and ch.isalnum() else "x" for ch in use_family)
        return f"lbl-{safe}-{suffix}", fontfile
    if _has_cjk(text):
        return "china-s", resolve_pdf_font()
    return "helv", None


def _textbox_fits(text: str, width: float, height: float, fontsize: float, fontname: str, fontfile: str | None, align: int) -> bool:
    if width < 1 or height < 1:
        return False
    doc = fitz.open()
    try:
        page = doc.new_page(width=max(width + 20, 40), height=max(height + 20, 40))
        kwargs: dict[str, Any] = {"fontsize": fontsize, "fontname": fontname, "align": align}
        if fontfile:
            kwargs["fontfile"] = fontfile
        return page.insert_textbox(fitz.Rect(0, 0, width, height), text, **kwargs) >= 0
    finally:
        doc.close()


def _insert_text(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    *,
    font_mm: float,
    family: str,
    bold: bool,
    italic: bool,
    underline: bool,
    align: str,
    wrap: bool,
) -> None:
    if not text or float(font_mm or 0) <= 0:
        return
    content = text if wrap else text.replace("\n", " ")
    fontname, fontfile = _resolve_label_font(family, bold, content)
    align_code = ALIGN_MAP.get(align, 0)
    size = max(0.5, float(font_mm) * 72.0 / 25.4)
    fitted = size
    while fitted >= 4.0:
        if _textbox_fits(content, rect.width, rect.height, fitted, fontname, fontfile, align_code):
            break
        if fitted <= 4.0:
            break
        fitted = max(4.0, fitted - 0.5)
    kwargs: dict[str, Any] = {
        "fontsize": fitted,
        "fontname": fontname,
        "align": align_code,
    }
    if fontfile:
        kwargs["fontfile"] = fontfile
    page.insert_textbox(rect, content, **kwargs)
    if underline:
        y = min(rect.y1 - 0.4, rect.y0 + fitted + 0.6)
        page.draw_line(fitz.Point(rect.x0, y), fitz.Point(rect.x1, y), color=(0, 0, 0), width=0.3)
    _ = italic


def scale_matrix_samples(matrix: list[list[int]], scale: int = 16) -> tuple[int, int, bytes]:
    """最近邻放大点阵，避免 PDF 把 32×32 图拉伸发糊导致扫不出。"""
    rows = len(matrix)
    cols = len(matrix[0]) if matrix else 0
    step = max(1, int(scale))
    if rows < 1 or cols < 1:
        return 0, 0, b""
    width = cols * step
    height = rows * step
    out = bytearray(width * height)
    offset = 0
    for row in matrix:
        expanded = bytes((0 if bit else 255) for bit in row for _ in range(step))
        for _ in range(step):
            out[offset : offset + width] = expanded
            offset += width
    return width, height, bytes(out)


def _disable_image_interpolate(page: fitz.Page, xref: int) -> None:
    if not xref:
        return
    try:
        page.parent.xref_set_key(xref, "Interpolate", "false")
    except Exception:
        pass


def _draw_datamatrix(page: fitz.Page, rect: fitz.Rect, payload: str, symbology: str = "ecc200") -> None:
    if str(symbology or "").strip().lower() == "qr":
        png = make_qr_png(payload)
        if not png:
            return
        xref = page.insert_image(rect, stream=png, keep_proportion=True)
        _disable_image_interpolate(page, xref)
        return
    matrix = encode_datamatrix(payload, min_symbol=32)
    width, height, samples = scale_matrix_samples(matrix, 16)
    if width < 1 or height < 1:
        return
    pix = fitz.Pixmap(fitz.csGRAY, width, height, samples, 0)
    try:
        xref = page.insert_image(rect, pixmap=pix, keep_proportion=True)
        _disable_image_interpolate(page, xref)
    finally:
        pix = None


def _draw_punch(page: fitz.Page, x0: float, y0: float, size: float) -> None:
    cx = x0 + size / 2
    cy = y0 + size / 2
    page.draw_circle(fitz.Point(cx, cy), size / 2, color=(0.86, 0.15, 0.15), fill=(0.86, 0.15, 0.15), width=0)
    page.draw_circle(fitz.Point(cx, cy), size * 0.31, color=(0.996, 0.792, 0.792), fill=(0.996, 0.792, 0.792), width=0)
    page.draw_circle(fitz.Point(cx, cy), size * 0.14, color=(0.60, 0.11, 0.11), fill=(0.60, 0.11, 0.11), width=0)


def _draw_label(
    page: fitz.Page,
    origin_x: float,
    origin_y: float,
    design: dict[str, Any],
    item: dict[str, Any],
) -> None:
    width = _mm(float(design.get("width_mm") or 0))
    height = _mm(float(design.get("height_mm") or 0))
    settings = design.get("print") or {}
    if bool(settings.get("border_enabled")):
        try:
            border_px = float(settings.get("border_width_px") or 1)
        except (TypeError, ValueError):
            border_px = 1.0
        border_px = max(0.5, min(8.0, round(border_px * 2) / 2))
        stroke_pt = border_px * 72.0 / 96.0
        inset = stroke_pt / 2
        box = fitz.Rect(
            origin_x + inset,
            origin_y + inset,
            origin_x + width - inset,
            origin_y + height - inset,
        )
        page.draw_rect(box, color=(0, 0, 0), width=stroke_pt)
    values = item_values(item)
    for mod in design.get("modules") or []:
        mtype = mod.get("type")
        x = origin_x + _mm(float(mod.get("x_mm") or 0))
        y = origin_y + _mm(float(mod.get("y_mm") or 0))
        if mtype == "punch":
            _draw_punch(page, x, y, _mm(float(mod.get("size_mm") or 6)))
            continue
        if mtype == "datamatrix":
            size = _mm(float(mod.get("size_mm") or 20))
            quiet = _mm(float(mod.get("quiet_mm") if mod.get("quiet_mm") is not None else 2.5))
            page.draw_rect(fitz.Rect(x, y, x + size, y + size), color=(1, 1, 1), fill=(1, 1, 1), width=0)
            inner = fitz.Rect(x + quiet, y + quiet, x + size - quiet, y + size - quiet)
            if inner.width > 0.5 and inner.height > 0.5:
                payload = resolve_datamatrix_payload(item, str(mod.get("content") or ""))
                _draw_datamatrix(page, inner, payload, str(mod.get("symbology") or "ecc200"))
            continue
        if mtype == "text":
            w = _mm(float(mod.get("w_mm") or 10))
            h = _mm(float(mod.get("h_mm") or 4))
            text = fill_placeholders(str(mod.get("content") or ""), values)
            _insert_text(
                page,
                fitz.Rect(x, y, x + w, y + h),
                text,
                font_mm=float(mod.get("font_mm") or 0.5),
                family=str(mod.get("font_family") or "Calibri"),
                bold=bool(mod.get("bold")),
                italic=bool(mod.get("italic")),
                underline=bool(mod.get("underline")),
                align=str(mod.get("align") or "left"),
                wrap=bool(mod.get("wrap")),
            )


def render_label_pdf(design: dict[str, Any], items: list[dict[str, Any]]) -> bytes:
    settings = design.get("print") or {}
    paper_w, paper_h = paper_size_mm(settings)
    cols = max(1, int(settings.get("cols") or 1))
    rows = max(1, int(settings.get("rows") or 1))
    gap_x = float(settings.get("gap_x_mm") or 0)
    gap_y = float(settings.get("gap_y_mm") or 0)
    margin = float(settings.get("margin_mm") or 0)
    label_w = float(design.get("width_mm") or 0)
    label_h = float(design.get("height_mm") or 0)
    per_page = cols * rows

    doc = fitz.open()
    try:
        for item in items:
            copies = max(0, int(item.get("quantity") or 0))
            if copies <= 0:
                continue
            pages = math.ceil(copies / per_page)
            for page_index in range(pages):
                page = doc.new_page(width=_mm(paper_w), height=_mm(paper_h))
                start = page_index * per_page
                count = min(per_page, copies - start)
                for slot in range(count):
                    col = slot % cols
                    row = slot // cols
                    x = _mm(margin + col * (label_w + gap_x))
                    y = _mm(margin + row * (label_h + gap_y))
                    _draw_label(page, x, y, design, item)
        if doc.page_count == 0:
            raise ValueError("没有可打印的标签（数量均为 0）")
        return doc.tobytes()
    finally:
        doc.close()
