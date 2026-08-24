"""规格型号拆分：规格（DN/NB/A）+ 型号/标准（PN/CL/LB/K）。"""
from __future__ import annotations

import re


def _normalize_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def extract_spec_standard(spec_model: str) -> tuple[str, str]:
    """返回 (规格, 型号)。型号对应实验建档的「标准」。"""
    parts = [p for p in re.split(r"\s+", _normalize_str(spec_model)) if p]
    spec = ""
    model = ""
    for p in parts:
        up = p.upper()
        if not spec and re.match(r"^(DN\d+|\d+NB|\d+A)$", up):
            spec = up
        if not model and re.match(r"^(PN\d+|CL\d+|\d+LB|\d+K)$", up):
            model = up
    return spec, model
