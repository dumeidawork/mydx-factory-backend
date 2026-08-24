"""图纸号-版本号：入库 Rev 形，文件名连字符形，订单匹配兼容两种写法。"""
from __future__ import annotations

import re

_REV_SPLIT = re.compile(r"\s+Rev", re.IGNORECASE)


def normalize_drawing_rev_for_db(raw: str) -> str:
    """入库：已有「空格+Rev」则统一 Rev 大小写；否则把最后一个 - 换成「 Rev」。"""
    text = (raw or "").strip()
    if not text:
        return ""
    match = _REV_SPLIT.search(text)
    if match:
        return f"{text[: match.start()]} Rev{text[match.end() :]}"
    idx = text.rfind("-")
    if idx > 0:
        return f"{text[:idx]} Rev{text[idx + 1 :]}"
    return text


def drawing_rev_for_filename(db_form: str) -> str:
    """拼文件名：把「半角空格+Rev」换成 -。"""
    text = (db_form or "").strip()
    if not text:
        return ""
    return _REV_SPLIT.sub("-", text, count=1)


def drawing_no_match_variants(raw: str) -> list[str]:
    """订单匹配用：原文、Rev 形、连字符形去重。"""
    text = (raw or "").strip()
    if not text:
        return []
    db_form = normalize_drawing_rev_for_db(text)
    variants: list[str] = []
    for item in (text, db_form, drawing_rev_for_filename(db_form), drawing_rev_for_filename(text)):
        item = (item or "").strip()
        if item and item not in variants:
            variants.append(item)
    return variants


def looks_like_drawing_rev(raw: str) -> bool:
    text = (raw or "").strip()
    if not text:
        return False
    if _REV_SPLIT.search(text):
        return True
    return bool(re.search(r"-\d+[A-Za-z0-9]*$", text))
