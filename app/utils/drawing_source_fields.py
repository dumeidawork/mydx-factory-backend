"""图纸尺寸表字段并集与表头别名。前后端约定同一套 key。"""
from __future__ import annotations

import re

DIM_FIELDS = (
    "od_d",
    "id_c",
    "thk_t",
    "thk_f",
    "mouth_m",
    "mouth_n",
    "center_k",
    "seat_a",
    "seat_h_f",
    "id_small_d",
    "id_large_e",
    "r_val",
    "r1_val",
    "r2_val",
    "e_val",
    "s_neck",
    "hole_h",
    "hole_n",
    "hole_h2",
    "hole_n2",
    "pcd2",
    "thru_c",
    "side_hole",
    "side_hole_depth",
    "side_a",
    "side_a_depth",
    "side_b",
    "side_b_depth",
    "bottom_hole",
    "weld_theta",
    "v1_deg",
    "v2_deg",
    "g_val",
    "f_depth",
    "unit_weight",
)

EXISTING_DIM_FIELDS = (
    "od_d",
    "id_c",
    "thk_t",
    "thk_f",
    "mouth_m",
    "center_k",
    "seat_a",
    "seat_h_f",
    "r_val",
    "hole_h",
    "hole_n",
    "side_b",
    "side_b_depth",
    "unit_weight",
)

NEW_DIM_FIELDS = tuple(k for k in DIM_FIELDS if k not in EXISTING_DIM_FIELDS)

DIM_LABELS: dict[str, str] = {
    "spec_model": "规格型号",
    "od_d": "外径",
    "id_c": "内径",
    "thk_t": "厚度",
    "thk_f": "总厚度",
    "mouth_m": "上口",
    "mouth_n": "下口",
    "center_k": "中心距",
    "seat_a": "台径",
    "seat_h_f": "台高",
    "id_small_d": "小内径",
    "id_large_e": "大内径",
    "r_val": "R",
    "r1_val": "R1",
    "r2_val": "R2",
    "e_val": "E",
    "s_neck": "嘴部厚度",
    "hole_h": "孔径",
    "hole_n": "孔数",
    "hole_h2": "H2孔径",
    "hole_n2": "N2孔数",
    "pcd2": "PCD2中心距",
    "thru_c": "通孔C",
    "side_hole": "侧孔",
    "side_hole_depth": "侧孔深度",
    "side_a": "侧孔A",
    "side_a_depth": "侧孔A深度",
    "side_b": "侧孔B",
    "side_b_depth": "侧孔B深度",
    "bottom_hole": "底孔",
    "weld_theta": "焊口θ",
    "v1_deg": "V1°",
    "v2_deg": "V2°",
    "g_val": "G",
    "f_depth": "F深度",
    "unit_weight": "单重",
}

# 别名按「长的优先」匹配，避免「侧孔」抢掉「侧孔A深度」。
_RAW_ALIASES: dict[str, tuple[str, ...]] = {
    "spec_model": ("规格型号", "规格"),
    "od_d": ("D外径", "A外径", "外径"),
    "id_c": ("C内径", "B内径", "内径"),
    "thk_t": ("T厚度", "B厚度", "C厚度", "盘厚", "厚度"),
    "thk_f": ("F总厚度", "总厚度", "总厚"),
    "mouth_m": ("M上口", "F上口", "上口"),
    "mouth_n": ("N下口", "下口"),
    "center_k": ("K中心距", "S11中心距", "S11", "中心距"),
    "seat_a": ("A台径", "RF台径", "台径"),
    "seat_h_f": ("f台高", "F台高", "f1台高", "台高"),
    "id_small_d": ("D小内径", "小内径"),
    "id_large_e": ("E大内径", "大内径"),
    "r_val": ("R值",),
    "r1_val": ("R1",),
    "r2_val": ("R2",),
    "e_val": ("E≧", "E≥"),
    "s_neck": ("嘴部厚度", "S嘴部厚度"),
    "hole_h": ("H1孔径", "H孔径", "S9孔径", "S9", "孔径"),
    "hole_n": ("N1孔数", "N孔数", "S10孔数", "S10", "孔数"),
    "hole_h2": ("H2孔径", "H2"),
    "hole_n2": ("N2孔数", "N2"),
    "pcd2": ("PCD2中心距", "PCD2"),
    "thru_c": ("通孔C", "通孔"),
    "side_hole_depth": ("侧孔深度最小", "侧孔深度"),
    "side_hole": ("侧孔",),
    "side_a_depth": ("侧孔A深度最小", "侧孔A深度"),
    "side_a": ("侧孔A",),
    "side_b_depth": ("侧孔B深度",),
    "side_b": ("侧孔B",),
    "bottom_hole": ("底孔深度", "底孔"),
    "weld_theta": ("焊口θ", "焊口"),
    "v1_deg": ("V1°", "V1"),
    "v2_deg": ("V2°", "V2"),
    "g_val": (),
    "f_depth": ("F深度",),
    "unit_weight": ("单重",),
}

_SKIP_ALIASES = (
    "件数",
    "数量",
    "数量件",
    "数量(件)",
    "序号",
    "名称",
    "备注",
)

_PAREN = re.compile(r"[（(]([^）)]*)[）)]")
_WS = re.compile(r"\s+")
_TRAIL_TOL = re.compile(r"[±+\-－—]\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?$")


def normalize_header(raw: str) -> str:
    text = str(raw or "").replace("\u00a0", " ")
    inners = [m.group(1) for m in _PAREN.finditer(text)]
    text = _PAREN.sub("", text)
    if inners:
        text = text + "".join(inners)
    text = _WS.sub("", text)
    text = text.replace("*", "")
    text = _TRAIL_TOL.sub("", text)
    text = text.replace("≧", "").replace("≥", "").replace("°", "")
    return text.casefold()


def _build_maps() -> tuple[dict[str, str], list[tuple[str, str]], set[str]]:
    exact: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    for key, aliases in _RAW_ALIASES.items():
        label = DIM_LABELS.get(key, key)
        for alias in (label, key, *aliases):
            n = normalize_header(alias)
            if not n:
                continue
            exact.setdefault(n, key)
            pairs.append((n, key))
    skip = {normalize_header(s) for s in _SKIP_ALIASES}
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    return exact, pairs, skip


_EXACT, _PAIRS, _SKIP = _build_maps()

# 单字母列（WN 上行）仅在无中文名时作弱匹配，避免 d 误当成外径。
_WEAK_LETTER = {
    normalize_header("D"): "od_d",
    normalize_header("B"): "id_c",
    normalize_header("C"): "thk_t",
    normalize_header("H"): "thk_f",
    normalize_header("A"): "mouth_m",
    normalize_header("N"): "mouth_n",
    normalize_header("K"): "center_k",
    normalize_header("d"): "seat_a",
    normalize_header("f1"): "seat_h_f",
    normalize_header("t"): "seat_h_f",
    normalize_header("L"): "hole_h",
    normalize_header("h"): "hole_h",
    normalize_header("n"): "hole_n",
    normalize_header("E"): "e_val",
    normalize_header("S"): "s_neck",
    normalize_header("G"): "g_val",
    normalize_header("R"): "r_val",
}


def match_header(raw: str) -> str | None:
    """把 Excel 表头映射到字段 key；无法识别或应忽略时返回 None。"""
    n = normalize_header(raw)
    if not n or n in _SKIP:
        return None
    if n in _EXACT:
        return _EXACT[n]
    best_key = None
    best_len = 0
    for alias, key in _PAIRS:
        if alias and alias in n and len(alias) > best_len:
            best_key = key
            best_len = len(alias)
    if best_key:
        return best_key
    return _WEAK_LETTER.get(n)


BOM_HEADER_MARKERS = frozenset(
    {normalize_header("序号"), normalize_header("名称"), normalize_header("物料号")}
)
DIM_HEADER_HINTS = frozenset(
    {
        normalize_header("外径"),
        normalize_header("内径"),
        normalize_header("厚度"),
        normalize_header("规格型号"),
        normalize_header("盘厚"),
    }
)
