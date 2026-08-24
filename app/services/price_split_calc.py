"""价格拆分表公式引擎。与源 Excel 公式对齐，导入与逐条录入共用。"""
from __future__ import annotations

from typing import Any

from app.services.price_split_schema import ALL_DATA_COLUMNS, DERIVED_FIELDS


def _f(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


_NUMERIC_KEYS = {
    "dn",
    "year_usage",
    "steel_price",
    "blanking_weight",
    "ht_blanking_weight",
    "forged_weight",
    "ht_loss",
    "gas_loss",
    "heat_loss",
    "net_weight",
    "recover_weight",
    "scrap_price",
    "blanking_cost",
    "forging_cost",
    "heat_treatment_cost",
    "ht_freight_cost",
    "ring_rolling_cost",
    "machining_cost",
    "ht_machining_extra",
    "drilling_cost",
    "grounding_hole_cost",
    "hoisting_hole_cost",
    "through_hole_cost",
    "ptfe_hole_cost",
    "m6_hole_cost",
    "packing_cost",
    "other_cost",
    "transport_cost",
    "port_surcharge",
    "profit_rate",
    "material_cost",
    "total_process_cost",
    "net_price",
    "excel_net_price",
    "vat_rate",
    "tax_inclusive_price",
}


def empty_row(material_group: str = "A105") -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key in ALL_DATA_COLUMNS:
        row[key] = 0 if key in _NUMERIC_KEYS else ""
    row["material_group"] = material_group
    row["profit_rate"] = 1.07
    row["year_usage"] = 0
    row["source_row"] = None
    row["excel_net_price"] = None
    row["vat_rate"] = None
    return row


def _apply_tax_inclusive(row: dict[str, Any], region: str) -> None:
    if region == "france":
        row["tax_inclusive_price"] = _f(row.get("net_price")) * (1 + _f(row.get("vat_rate")) / 100.0)
    else:
        row["tax_inclusive_price"] = 0
        row["vat_rate"] = None


def apply_derived(row: dict[str, Any], region: str) -> dict[str, Any]:
    """按材质组与区域重算派生列，写回 row 并返回。"""
    group = str(row.get("material_group") or "A105").upper()
    if group == "SS":
        _apply_ss(row, region)
    else:
        _apply_a105(row, region)
    _apply_tax_inclusive(row, region)
    return row


def _apply_a105(row: dict[str, Any], region: str) -> None:
    blanking = _f(row.get("blanking_weight"))
    gas_loss = _f(row.get("gas_loss"))
    net_weight = _f(row.get("net_weight"))
    steel = _f(row.get("steel_price"))
    scrap = _f(row.get("scrap_price"))
    profit = _f(row.get("profit_rate"), 1.07)
    transport = _f(row.get("transport_cost"))
    port = _f(row.get("port_surcharge"))

    ht_loss = gas_loss * 2
    denom = 1 - ht_loss
    ht_blanking = blanking / denom if abs(denom) > 1e-12 else 0.0
    forged = ht_blanking * (1 - gas_loss - ht_loss)
    recover = forged - net_weight
    material_cost = (steel * ht_blanking - recover * scrap) / 1000.0

    process_sum = (
        _f(row.get("blanking_cost"))
        + _f(row.get("forging_cost"))
        + _f(row.get("ring_rolling_cost"))
        + _f(row.get("machining_cost"))
        + _f(row.get("drilling_cost"))
        + _f(row.get("grounding_hole_cost"))
        + _f(row.get("hoisting_hole_cost"))
        + _f(row.get("packing_cost"))
        + _f(row.get("other_cost"))
        + _f(row.get("ptfe_hole_cost"))
        + _f(row.get("m6_hole_cost"))
        + _f(row.get("ht_machining_extra"))
    )
    ht_part = (_f(row.get("heat_treatment_cost")) + _f(row.get("ht_freight_cost"))) * ht_blanking * (1 - gas_loss) / 1000.0
    total_process = process_sum * net_weight / 1000.0 + ht_part

    freight = transport + port if region == "france" else transport
    net_price = (material_cost + total_process) * profit + freight * net_weight / 1000.0

    row["ht_loss"] = ht_loss
    row["ht_blanking_weight"] = ht_blanking
    row["forged_weight"] = forged
    row["recover_weight"] = recover
    row["material_cost"] = material_cost
    row["total_process_cost"] = total_process
    row["net_price"] = net_price
    row["heat_loss"] = 0


def _apply_ss(row: dict[str, Any], region: str) -> None:
    blanking = _f(row.get("blanking_weight"))
    heat_loss = _f(row.get("heat_loss"))
    net_weight = _f(row.get("net_weight"))
    steel = _f(row.get("steel_price"))
    scrap = _f(row.get("scrap_price"))
    profit = _f(row.get("profit_rate"), 1.07)
    transport = _f(row.get("transport_cost"))
    port = _f(row.get("port_surcharge"))

    forged = blanking * (1 - heat_loss)
    recover = forged - net_weight
    material_cost = (steel * blanking - recover * scrap) / 1000.0

    # 源表加工成本：费率合计 * 2 * 净重 / 1000。
    # 大连不锈钢公式中车机加工费出现两次，按表实现。
    machining = _f(row.get("machining_cost"))
    process_sum = (
        _f(row.get("blanking_cost"))
        + _f(row.get("forging_cost"))
        + _f(row.get("ring_rolling_cost"))
        + machining
        + machining
        + _f(row.get("drilling_cost"))
        + _f(row.get("ptfe_hole_cost"))
        + _f(row.get("m6_hole_cost"))
        + _f(row.get("packing_cost"))
        + _f(row.get("other_cost"))
        + _f(row.get("hoisting_hole_cost"))
        + _f(row.get("through_hole_cost"))
    )
    total_process = process_sum * 2 * net_weight / 1000.0
    freight = transport + port if region == "france" else transport
    net_price = (material_cost + total_process) * profit + freight * net_weight / 1000.0

    row["heat_loss"] = heat_loss
    row["forged_weight"] = forged
    row["recover_weight"] = recover
    row["material_cost"] = material_cost
    row["total_process_cost"] = total_process
    row["net_price"] = net_price
    row["ht_loss"] = 0
    row["ht_blanking_weight"] = 0
    row["gas_loss"] = 0


def is_derived_field(name: str) -> bool:
    return name in DERIVED_FIELDS
