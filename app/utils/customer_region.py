"""客户地区判断：合同总金额等规则按客户名区分大连/法国。"""
from __future__ import annotations

import re

# 与前端生产页大连客户选项一致
DALIAN_CONTRACT_CUSTOMER = "Siemens Sensors＆Communication Ltd."

_MULTI_SPACE = re.compile(r"\s+")


def normalize_customer_name(name: str | None) -> str:
    text = (name or "").strip()
    text = _MULTI_SPACE.sub(" ", text)
    return text.replace("＆", "&")


def is_dalian_contract_customer(name: str | None) -> bool:
    if not name:
        return False
    return normalize_customer_name(name) == normalize_customer_name(DALIAN_CONTRACT_CUSTOMER)
