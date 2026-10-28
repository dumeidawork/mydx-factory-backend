"""规格型号拆分：物料号、标准号、规格、型号、类型。"""
from __future__ import annotations

import re
from typing import TypedDict

_SPEC_RE = re.compile(r"^(DN\d+|\d+NB|\d+A)$", re.IGNORECASE)
_MODEL_RE = re.compile(r"^(PN\d+|CL\d+|\d+LB|\d+K)$", re.IGNORECASE)


class SpecModelFields(TypedDict):
    material_no: str
    standard_no: str
    spec: str
    model: str
    drawing_type: str


def _normalize_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def extract_spec_model_fields(spec_model: str) -> SpecModelFields:
    """从规格型号拆出物料号 / 标准号 / 规格 / 型号 / 类型。"""
    parts = [p for p in re.split(r"\s+", _normalize_str(spec_model)) if p]
    empty: SpecModelFields = {
        "material_no": "",
        "standard_no": "",
        "spec": "",
        "model": "",
        "drawing_type": "",
    }
    if not parts:
        return empty

    spec = ""
    model = ""
    spec_idx: int | None = None
    model_idx: int | None = None
    for index, part in enumerate(parts):
        up = part.upper()
        if spec_idx is None and _SPEC_RE.match(up):
            spec_idx = index
            spec = up
        if model_idx is None and _MODEL_RE.match(up):
            model_idx = index
            model = up

    bounds = [i for i in (spec_idx, model_idx) if i is not None]
    standard_no = ""
    drawing_type = ""
    if bounds:
        start = min(bounds)
        end = max(bounds)
        if start > 1:
            standard_no = " ".join(parts[1:start]).strip()
        drawing_type = " ".join(parts[end + 1 :]).strip()

    return {
        "material_no": parts[0],
        "standard_no": standard_no,
        "spec": spec,
        "model": model,
        "drawing_type": drawing_type,
    }


def extract_spec_standard(spec_model: str) -> tuple[str, str]:
    """返回 (规格, 型号)。型号对应实验建档的「标准」。"""
    fields = extract_spec_model_fields(spec_model)
    return fields["spec"], fields["model"]


def spec_model_for_storage(spec_model: str, material_no: str = "") -> str:
    """入库用规格型号：去掉开头与物料号相同的第一段。"""
    text = _normalize_str(spec_model)
    parts = [p for p in re.split(r"\s+", text) if p]
    if not parts:
        return ""
    mat = _normalize_str(material_no) or parts[0]
    if parts[0].casefold() == mat.casefold():
        return " ".join(parts[1:]).strip()
    return text
