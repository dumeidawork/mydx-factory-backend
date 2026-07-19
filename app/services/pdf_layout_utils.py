"""PDF 模板 span 布局工具：占位符定位与单元格估算。"""
from __future__ import annotations

import re
from typing import Any

import fitz

PLACEHOLDER_RE = re.compile(r"\{\{([^}]+)\}\}")


def collect_page_spans(page: fitz.Page) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            line_bbox = fitz.Rect(line["bbox"])
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text:
                    continue
                items.append(
                    {
                        "text": text,
                        "bbox": fitz.Rect(span["bbox"]),
                        "line_bbox": line_bbox,
                        "size": float(span.get("size", 10) or 10),
                    }
                )
    return items


def build_span_index(items: list[dict[str, Any]]) -> tuple[str, list[tuple[int, int, int]]]:
    parts: list[str] = []
    ranges: list[tuple[int, int, int]] = []
    offset = 0
    for idx, item in enumerate(items):
        text = item["text"]
        start = offset
        offset += len(text)
        parts.append(text)
        ranges.append((start, offset, idx))
    return "".join(parts), ranges


def bbox_for_range(
    items: list[dict[str, Any]],
    ranges: list[tuple[int, int, int]],
    start: int,
    end: int,
) -> fitz.Rect | None:
    rect: fitz.Rect | None = None
    for span_start, span_end, idx in ranges:
        if span_end <= start or span_start >= end:
            continue
        item_rect = items[idx]["bbox"]
        rect = item_rect if rect is None else rect | item_rect
    return rect


def span_bboxes_for_range(
    items: list[dict[str, Any]],
    ranges: list[tuple[int, int, int]],
    start: int,
    end: int,
) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []
    for span_start, span_end, idx in ranges:
        if span_end <= start or span_start >= end:
            continue
        rects.append(items[idx]["bbox"])
    return rects


def line_bbox_for_range(
    items: list[dict[str, Any]],
    ranges: list[tuple[int, int, int]],
    start: int,
    end: int,
) -> fitz.Rect | None:
    rect: fitz.Rect | None = None
    for span_start, span_end, idx in ranges:
        if span_end <= start or span_start >= end:
            continue
        line_rect = items[idx]["line_bbox"]
        rect = line_rect if rect is None else rect | line_rect
    return rect


def same_line(a: fitz.Rect, b: fitz.Rect, tol: float = 0.5) -> bool:
    return abs(a.y0 - b.y0) <= tol and abs(a.y1 - b.y1) <= tol


def cell_rect_for_match(
    items: list[dict[str, Any]],
    ranges: list[tuple[int, int, int]],
    start: int,
    end: int,
) -> fitz.Rect | None:
    ph_bbox = bbox_for_range(items, ranges, start, end)
    line_bbox = line_bbox_for_range(items, ranges, start, end)
    if ph_bbox is None:
        return None
    if line_bbox is None:
        return ph_bbox

    match_indices = [
        idx
        for span_start, span_end, idx in ranges
        if not (span_end <= start or span_start >= end)
    ]
    line_keys = {tuple(items[i]["line_bbox"]) for i in match_indices}
    if len(line_keys) > 1:
        return line_bbox

    ref_line = items[match_indices[0]]["line_bbox"]
    line_text = "".join(
        it["text"] for it in items if same_line(it["line_bbox"], ref_line)
    )
    fixed_text = PLACEHOLDER_RE.sub("", line_text).strip()
    if fixed_text and line_bbox.width > ph_bbox.width * 1.15:
        return fitz.Rect(ph_bbox.x0, line_bbox.y0, ph_bbox.x1, line_bbox.y1)
    return line_bbox


def size_for_range(
    items: list[dict[str, Any]],
    ranges: list[tuple[int, int, int]],
    start: int,
    end: int,
) -> float:
    for span_start, span_end, idx in ranges:
        if span_end <= start or span_start >= end:
            continue
        return float(items[idx]["size"])
    return 10.0


def find_placeholder_matches(full_text: str) -> list[re.Match[str]]:
    return list(PLACEHOLDER_RE.finditer(full_text))


def rect_to_list(rect: fitz.Rect, *, digits: int = 1) -> list[float]:
    return [round(rect.x0, digits), round(rect.y0, digits), round(rect.x1, digits), round(rect.y1, digits)]


def list_to_rect(values: list[float]) -> fitz.Rect:
    return fitz.Rect(values[0], values[1], values[2], values[3])


def extract_placeholder_layout(page: fitz.Page, page_index: int = 0) -> list[dict[str, Any]]:
    """从单页 span 提取 {{key}} 占位符布局。"""
    items = collect_page_spans(page)
    if not items:
        return []
    full, ranges = build_span_index(items)
    fields: list[dict[str, Any]] = []
    for match in find_placeholder_matches(full):
        key = match.group(1).strip()
        cell = cell_rect_for_match(items, ranges, match.start(), match.end())
        if cell is None:
            continue
        font_size = size_for_range(items, ranges, match.start(), match.end())
        multiline = cell.height > font_size * 1.8
        fields.append(
            {
                "name": key,
                "page": page_index,
                "rect": rect_to_list(cell),
                "font_size": round(font_size, 1),
                "align": "center",
                "multiline": multiline,
            }
        )
    return fields


def extract_pdf_placeholder_layout(pdf_path: fitz.Document | str) -> dict[str, Any]:
    """从 PDF 提取全部占位符字段布局。"""
    own_doc = not isinstance(pdf_path, fitz.Document)
    doc = fitz.open(str(pdf_path)) if own_doc else pdf_path
    fields: list[dict[str, Any]] = []
    try:
        page_count = doc.page_count
        for page_index, page in enumerate(doc):
            fields.extend(extract_placeholder_layout(page, page_index))
    finally:
        if own_doc:
            doc.close()
    names = [f["name"] for f in fields]
    return {
        "page_count": page_count,
        "field_count": len(fields),
        "unique_names": sorted(set(names)),
        "fields": fields,
    }
