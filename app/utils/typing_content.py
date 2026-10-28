"""打字内容拼接：标准号 规格 型号 + 打字材质 + 炉号 + 热处理批号 + 铭远厂标。

纯函数，不访问数据库；DN-英寸与材质映射由调用方查好后传入。
"""
from __future__ import annotations

import re
from typing import TypedDict

_SPEC_RE = re.compile(r"^(DN\d+|\d+NB|\d+A)$", re.IGNORECASE)
_MODEL_RE = re.compile(r"^(PN\d+|CL\d+|\d+LB|\d+K)$", re.IGNORECASE)
_MATERIAL_NO_RE = re.compile(r"^(A5E\w+|FDK-\S+)$", re.IGNORECASE)
_DN_NUM_RE = re.compile(r"^DN(\d+)$", re.IGNORECASE)
_QR_DN_RE = re.compile(r"DN(\d+(?:-\d+)?)", re.IGNORECASE)
_QR_NB_RE = re.compile(r"(\d+(?:-\d+)?)NB", re.IGNORECASE)
_QR_PN_RE = re.compile(r"PN(\d+(?:-\d+)?)", re.IGNORECASE)
_QR_CL_RE = re.compile(r"CL(\d+(?:-\d+)?)", re.IGNORECASE)
_QR_LB_RE = re.compile(r"(\d+(?:-\d+)?)LB", re.IGNORECASE)
_QR_K_RE = re.compile(r"(\d+(?:-\d+)?)K\b", re.IGNORECASE)

FACTORY_MARK = "铭远厂标"
ASME_B165 = "ASME B16.5"

# 打字内容里的英寸沿用模板的全角右双引号
INCH_QUOTE = "”"


class SpecHead(TypedDict):
    standard_no: str
    spec: str
    model: str
    drawing_type: str


class TypingContentResult(TypedDict):
    content: str
    standard_no: str
    spec: str
    model: str
    spec_display: str
    typing_material: str
    missing: list[str]


def _normalize_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _collapse_spaces(value: object) -> str:
    return " ".join(_normalize_str(value).split())


def normalize_material_key(raw: object) -> str:
    """材质映射键：去空格转大写，A105 / 304 / 316 之类直接对齐。"""
    return _collapse_spaces(raw).upper()


def to_full_width_inch(raw: object) -> str:
    """英寸表存半角 " ，打字内容用模板的全角 ” 。"""
    text = _normalize_str(raw)
    if not text:
        return ""
    for ch in ('"', "＂", "″", "“"):
        text = text.replace(ch, INCH_QUOTE)
    return text


def split_spec_head(spec_model: object, material_no: object = "", part_no: object = "") -> SpecHead:
    """拆规格型号。与 extract_spec_model_fields 不同：不假定首段一定是物料号。

    标准号取规格/型号之前的全部 token，因此支持 ASME B16.5 这种带空格的标准号。
    """
    parts = _collapse_spaces(spec_model).split(" ")
    parts = [p for p in parts if p]
    empty: SpecHead = {"standard_no": "", "spec": "", "model": "", "drawing_type": ""}
    if not parts:
        return empty

    known = {normalize_material_key(material_no), normalize_material_key(part_no)}
    known.discard("")
    while parts:
        head = parts[0]
        if normalize_material_key(head) in known or _MATERIAL_NO_RE.match(head):
            parts = parts[1:]
            continue
        break
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
    if not bounds:
        return {"standard_no": " ".join(parts), "spec": "", "model": "", "drawing_type": ""}
    start = min(bounds)
    end = max(bounds)
    return {
        "standard_no": " ".join(parts[:start]),
        "spec": spec,
        "model": model,
        "drawing_type": " ".join(parts[end + 1 :]),
    }


def is_asme_b165(standard_no: object) -> bool:
    return _collapse_spaces(standard_no).upper() == ASME_B165


def build_spec_display(head: SpecHead, inch_by_dn: dict[str, str] | None = None) -> tuple[str, list[str]]:
    """规格段。ASME B16.5 时拼成 DN300/NPS12” ，其余直接用规格。"""
    spec = head.get("spec") or ""
    if not spec:
        return "", ["spec"]
    if not is_asme_b165(head.get("standard_no")):
        return spec, []
    inch = to_full_width_inch((inch_by_dn or {}).get(spec.upper(), ""))
    if not inch:
        return spec, ["inch_value"]
    return f"{spec}/NPS{inch}", []


def build_typing_content(
    *,
    spec_model: object,
    material: object,
    material_no: object = "",
    part_no: object = "",
    heat_no: object = "",
    heat_treatment_batch_no: object = "",
    inch_by_dn: dict[str, str] | None = None,
    typing_material_by_key: dict[str, str] | None = None,
) -> TypingContentResult:
    """五段拼接。任一段缺失时该段留空，字段名记入 missing 供页面/导出标粉色。"""
    head = split_spec_head(spec_model, material_no, part_no)
    missing: list[str] = []
    if not _collapse_spaces(spec_model):
        missing.append("spec_model")

    spec_display, spec_missing = build_spec_display(head, inch_by_dn)
    missing.extend(spec_missing)

    material_key = normalize_material_key(material)
    typing_material = (typing_material_by_key or {}).get(material_key, "")
    if not typing_material:
        missing.append("typing_material")

    heat = _collapse_spaces(heat_no)
    if not heat:
        missing.append("heat_no")
    batch = _collapse_spaces(heat_treatment_batch_no)
    if not batch:
        missing.append("heat_treatment_batch_no")

    segments = [
        head.get("standard_no") or "",
        spec_display,
        head.get("model") or "",
        typing_material,
        heat,
        batch,
        FACTORY_MARK,
    ]
    content = " ".join(seg for seg in segments if seg)

    return {
        "content": content,
        "standard_no": head.get("standard_no") or "",
        "spec": head.get("spec") or "",
        "model": head.get("model") or "",
        "spec_display": spec_display,
        "typing_material": typing_material,
        "missing": missing,
    }


def format_qr_spec(spec: object, model: object = "") -> str:
    """把规格+型号拼成二维码用的 DN 在前规格串。对不上则返回空串。"""
    blob = f"{_normalize_str(spec)} {_normalize_str(model)}"
    if not blob.strip():
        return ""

    dn = ""
    dn_match = _QR_DN_RE.search(blob)
    if dn_match:
        dn = f"DN{dn_match.group(1)}"
    else:
        nb_match = _QR_NB_RE.search(blob)
        if nb_match:
            dn = f"DN{nb_match.group(1)}"

    pressure = ""
    cl_match = _QR_CL_RE.search(blob)
    if cl_match:
        pressure = f"CL{cl_match.group(1)}"
    else:
        lb_match = _QR_LB_RE.search(blob)
        if lb_match:
            pressure = f"CL{lb_match.group(1)}"
        else:
            pn_match = _QR_PN_RE.search(blob)
            if pn_match:
                pressure = f"PN{pn_match.group(1)}"
            else:
                k_match = _QR_K_RE.search(blob)
                if k_match:
                    pressure = f"{k_match.group(1)}K"

    if dn and pressure:
        return f"{dn}/{pressure}".upper()
    if dn:
        return dn.upper()
    return ""


def dn_sort_key(dn_value: object) -> tuple[int, str]:
    text = _normalize_str(dn_value)
    match = _DN_NUM_RE.match(text)
    if match:
        return (int(match.group(1)), text)
    return (10**9, text)
