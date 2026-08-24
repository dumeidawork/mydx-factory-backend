"""热处理与测试模块API。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.core.database import execute, fetch_all, fetch_one
from app.core.file_log import append_log_line
from app.core.paths import get_storage_dir
from app.services.in_memory_store import heat_treatment_test_records

router = APIRouter(prefix="/heat-treatment", tags=["热处理与测试"])

CHEM_KEYS = ["C", "Mn", "P", "S", "Si", "Cu", "Ni", "Cr", "Mo", "V", "CEQ"]
CHEMICAL_LIMITS = {
    "C": ("<=", 0.23),
    "Mn": ("range", (0.60, 1.65)),
    "P": ("<=", 0.035),
    "S": ("<=", 0.025),
    "Si": ("range", (0.10, 0.35)),
    "Cu": ("<=", 0.40),
    "Ni": ("<=", 0.40),
    "Cr": ("<=", 0.30),
    "Mo": ("<=", 0.12),
    "V": ("<=", 0.08),
    "CEQ": ("<=", 0.36),
}

LOG_DIR = get_storage_dir() / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _append_log_line(file_name: str, line: str) -> None:
    append_log_line(LOG_DIR, file_name, line)


def _operator_name(user: CurrentUser) -> str:
    name = _normalize_str(user.name) or _normalize_str(user.username)
    if not name:
        raise HTTPException(status_code=400, detail="当前登录用户无有效姓名，无法记录更新者")
    return name


def _normalize_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _key(heat_no: str, batch_no: str, material_no: str = "") -> str:
    return f"{heat_no}::{batch_no}::{material_no}"


def _split_hardness(value: Any) -> tuple[str, str, str]:
    parts = [str(v).strip() for v in str(value or "").split("/") if str(v).strip()]
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], parts[2]


def _round_three(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return round(float(value), 3)


def _round_four(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return round(float(value), 4)


def _rounded_or_blank(value: Any) -> float | str:
    rounded = _round_three(value)
    return "" if rounded is None else rounded


def _parse_number(value: Any, field_name: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"{field_name}必须为数值") from exc


IMPACT_TEST_MIN = 27.0
IMPACT_TEST_MAX = 300.0


def _extract_impact_test_values(value: str) -> list[float]:
    """从冲击标准字符串提取全部数值。

    支持常见录入格式，例如：
    - T:198,150,141/R:198,146,148
    - T:42,45,50/R:38,33,46
    - T:218.182.156/R:221.184.158
    """
    return [float(match) for match in re.findall(r"\d+", _normalize_str(value))]


def _validate_impact_test(value: str) -> None:
    numbers = _extract_impact_test_values(value)
    if not numbers:
        raise HTTPException(status_code=400, detail="-20℃下冲击标准未识别到有效数值")
    out_of_range = [number for number in numbers if number < IMPACT_TEST_MIN or number > IMPACT_TEST_MAX]
    if out_of_range:
        bad = "、".join(f"{number:g}" for number in out_of_range)
        raise HTTPException(
            status_code=400,
            detail=f"-20℃下冲击标准单个值须在{IMPACT_TEST_MIN:g}～{IMPACT_TEST_MAX:g}之间，超出范围：{bad}",
        )


def _hardness_part_signature(part: Any) -> float | str:
    text = _normalize_str(part)
    if not text:
        return ""
    try:
        return float(text)
    except (TypeError, ValueError):
        return text


def _hardness_signature_from_parts(
    hardness_1: Any, hardness_2: Any, hardness_3: Any
) -> tuple[float | str, float | str, float | str]:
    return (
        _hardness_part_signature(hardness_1),
        _hardness_part_signature(hardness_2),
        _hardness_part_signature(hardness_3),
    )


def _hardness_signature(value: Any) -> tuple[float | str, float | str, float | str]:
    return _hardness_signature_from_parts(*_split_hardness(value))


def _impact_signature(value: Any) -> tuple[float, ...]:
    return tuple(_extract_impact_test_values(_normalize_str(value)))


def _format_hardness_display(value: Any) -> str:
    return "/".join(part for part in _split_hardness(value) if part)


def _collect_duplicate_trial_value_errors(
    normalized_rows: list[dict],
    own_ids: dict[int, int | None],
    db_rows: list[dict],
) -> list[dict]:
    hardness_db: dict[tuple[float | str, float | str, float | str], list[int]] = {}
    impact_db: dict[tuple[float, ...], list[int]] = {}
    for db_row in db_rows:
        db_id = int(db_row["id"])
        hardness_db.setdefault(
            _hardness_signature_from_parts(
                db_row.get("mech_hardness_1", ""),
                db_row.get("mech_hardness_2", ""),
                db_row.get("mech_hardness_3", ""),
            ),
            [],
        ).append(db_id)
        impact_db.setdefault(_impact_signature(db_row.get("mech_impact_test", "")), []).append(db_id)

    hardness_incoming: dict[tuple[float | str, float | str, float | str], list[int]] = {}
    impact_incoming: dict[tuple[float, ...], list[int]] = {}
    incoming_sigs: list[tuple[int, tuple[float | str, float | str, float | str], tuple[float, ...]]] = []
    for idx in sorted(own_ids):
        row = normalized_rows[idx]
        hardness_sig = _hardness_signature(row.get("hardness", ""))
        impact_sig = _impact_signature(row.get("impact_test", ""))
        hardness_incoming.setdefault(hardness_sig, []).append(idx)
        impact_incoming.setdefault(impact_sig, []).append(idx)
        incoming_sigs.append((idx, hardness_sig, impact_sig))

    errors: list[dict] = []
    for idx, hardness_sig, impact_sig in incoming_sigs:
        own_id = own_ids.get(idx)
        row = normalized_rows[idx]
        peer_hardness = [peer for peer in hardness_incoming.get(hardness_sig, []) if peer != idx]
        other_db_hardness = [db_id for db_id in hardness_db.get(hardness_sig, []) if db_id != own_id]
        if peer_hardness or other_db_hardness:
            display = _format_hardness_display(row.get("hardness", ""))
            errors.append(
                {
                    "index": idx,
                    "field": "hardness",
                    "message": f"第{idx + 1}行：硬度1/硬度2/硬度3 与已有记录重复（{display}）",
                }
            )
        peer_impact = [peer for peer in impact_incoming.get(impact_sig, []) if peer != idx]
        other_db_impact = [db_id for db_id in impact_db.get(impact_sig, []) if db_id != own_id]
        if peer_impact or other_db_impact:
            errors.append(
                {
                    "index": idx,
                    "field": "impact_test",
                    "message": f"第{idx + 1}行：-20℃下冲击标准 与已有记录重复",
                }
            )
    return errors


def _raise_duplicate_trial_values(errors: list[dict]) -> None:
    if not errors:
        return
    row_indexes = sorted({int(item["index"]) for item in errors})
    raise HTTPException(
        status_code=400,
        detail={
            "code": "duplicate_trial_values",
            "message": "\n".join(str(item["message"]) for item in errors),
            "row_indexes": row_indexes,
            "errors": errors,
        },
    )


def _format_failure(label: str, value: float) -> str:
    return f"{label}（{value:g}）"


def _check_mechanical_range(label: str, value: float | None, operator: str, threshold: float, failures: list[str]) -> None:
    if value is None:
        return
    if operator == ">=" and value < threshold:
        failures.append(_format_failure(label, value))


def _check_chemical_range(label: str, value: float | None, key: str, failures: list[str]) -> None:
    if value is None:
        return
    rule, target = CHEMICAL_LIMITS[key]
    if rule == "<=" and value > float(target):
        failures.append(_format_failure(label, value))
    if rule == "range":
        low, high = target
        if value < float(low) or value > float(high):
            failures.append(_format_failure(label, value))


def _evaluate_result(mechanical: dict, chemical: dict) -> tuple[str, str]:
    failures: list[str] = []
    checked_values = 0
    mechanical_checks = [
        ("屈服强度", _parse_number(mechanical.get("yield_strength"), "屈服强度"), ">=", 250.0),
        ("抗拉强度", _parse_number(mechanical.get("tensile_strength"), "抗拉强度"), ">=", 485.0),
        ("延伸标准", _parse_number(mechanical.get("elongation"), "延伸标准"), ">=", 22.0),
        ("Z标准", _parse_number(mechanical.get("reduction_area"), "Z标准"), ">=", 30.0),
    ]
    for label, value, operator, threshold in mechanical_checks:
        if value is not None:
            checked_values += 1
        _check_mechanical_range(label, value, operator, threshold, failures)

    for idx in range(1, 4):
        label = f"硬度{idx}"
        hardness = _parse_number(mechanical.get(f"hardness_{idx}"), label)
        if hardness is not None:
            checked_values += 1
            if hardness < 137 or hardness > 187:
                failures.append(_format_failure(label, hardness))

    for scope_key, scope_label in (("raw", "原材"), ("self", "自检")):
        scoped = chemical.get(scope_key, {}) or {}
        for key in CHEM_KEYS:
            value = _parse_number(scoped.get(key), f"{scope_label}{key}")
            if value is not None:
                checked_values += 1
            _check_chemical_range(f"{scope_label}{key}", value, key, failures)

    if failures:
        return "NG", "不合格：" + "、".join(failures)
    if checked_values:
        return "OK", ""
    return "", ""


def _calculate_ceq(values: dict[str, Any]) -> float | None:
    parsed = {key: _round_three(values.get(key)) for key in CHEM_KEYS if key != "CEQ"}
    if all(value is None for value in parsed.values()):
        return None
    c = parsed.get("C") or 0
    mn = parsed.get("Mn") or 0
    cr = parsed.get("Cr") or 0
    mo = parsed.get("Mo") or 0
    v = parsed.get("V") or 0
    ni = parsed.get("Ni") or 0
    cu = parsed.get("Cu") or 0
    return round(float(c) + float(mn) / 6 + (float(cr) + float(mo) + float(v)) / 5 + (float(ni) + float(cu)) / 15, 3)


def _empty_record(order_no: str, heat_no: str, batch_no: str, material_no: str = "") -> dict:
    return {
        "order_no": order_no,
        "heat_no": heat_no,
        "batch_no": batch_no,
        "material_no": material_no,
        "mechanical": {
            "test_temp": "RT",
            "yield_strength": "",
            "tensile_strength": "",
            "elongation": "",
            "reduction_area": "",
            "hardness": "",
            "hardness_1": "",
            "hardness_2": "",
            "hardness_3": "",
            "impact_test": "",
        },
        "chemical": {
            "raw": {k: "" for k in CHEM_KEYS},
            "self": {k: "" for k in CHEM_KEYS},
        },
        "test_result": "",
        "remark": "",
        "created_by": "",
    }


def _legacy_row_to_record(row: dict | None, order_no: str, heat_no: str, batch_no: str, material_no: str = "") -> dict:
    if not row:
        return _empty_record(order_no, heat_no, batch_no, material_no)
    hardness_1, hardness_2, hardness_3 = _split_hardness(row.get("mech_hardness", ""))
    return {
        "order_no": row.get("order_no", order_no),
        "heat_no": row["heat_no"],
        "batch_no": row["batch_no"],
        "material_no": row.get("material_no", material_no) or "",
        "mechanical": {
            "test_temp": row.get("mech_test_temp", "") or "RT",
            "yield_strength": row.get("mech_yield_strength", "") or "",
            "tensile_strength": row.get("mech_tensile_strength", "") or "",
            "elongation": row.get("mech_elongation", "") or "",
            "reduction_area": row.get("mech_reduction_area", "") or "",
            "hardness": row.get("mech_hardness", "") or "",
            "hardness_1": hardness_1,
            "hardness_2": hardness_2,
            "hardness_3": hardness_3,
            "impact_test": row.get("mech_impact_test", "") or "",
        },
        "chemical": {
            "raw": {key: _rounded_or_blank(row.get(f"chem_raw_{key.lower()}")) for key in CHEM_KEYS},
            "self": {key: _rounded_or_blank(row.get(f"chem_self_{key.lower()}")) for key in CHEM_KEYS},
        },
        "test_result": row.get("test_result", "") or "",
        "remark": row.get("remark", "") or "",
        "created_by": row.get("created_by", "") or "",
    }


def _trial_row_to_payload(row: dict | None) -> dict:
    if not row:
        return {
            "id": None,
            "test_date": "",
            "heat_no": "",
            "batch_no": "",
            "spec_model": "",
            "yield_strength": "",
            "tensile_strength": "",
            "elongation": "",
            "reduction_area": "",
            "hardness": "",
            "impact_test": "",
            "seq_no": "",
            "supplier_hardness": "",
            "material_no": "",
            "remark": "",
            "material": "",
            "updated_by": "",
            "updated_at": "",
        }
    return {
        "id": row.get("id"),
        "test_date": row.get("test_date", "") or "",
        "heat_no": row.get("heat_no", "") or "",
        "batch_no": row.get("batch_no", "") or "",
        "spec_model": row.get("spec_model", "") or "",
        "yield_strength": row.get("mech_yield_strength", "") or "",
        "tensile_strength": row.get("mech_tensile_strength", "") or "",
        "elongation": row.get("mech_elongation", "") or "",
        "reduction_area": row.get("mech_reduction_area", "") or "",
        "hardness": "/".join(
            [
                row.get("mech_hardness_1", "") or "",
                row.get("mech_hardness_2", "") or "",
                row.get("mech_hardness_3", "") or "",
            ]
        ).strip("/"),
        "impact_test": row.get("mech_impact_test", "") or "",
        "seq_no": row.get("seq_no", "") or "",
        "supplier_hardness": row.get("supplier_hardness", "") or "",
        "material_no": row.get("material_no", "") or "",
        "remark": row.get("remark", "") or "",
        "material": row.get("material", "") or "",
        "updated_by": row.get("updated_by", "") or "",
        "updated_at": str(row.get("updated_at") or ""),
    }


def _chemical_row_to_payload(row: dict | None) -> dict:
    return {
        "heat_no": row.get("heat_no", "") if row else "",
        "raw": {key: _rounded_or_blank(row.get(f"chem_raw_{key.lower()}")) if row else "" for key in CHEM_KEYS},
        "self": {key: _rounded_or_blank(row.get(f"chem_self_{key.lower()}")) if row else "" for key in CHEM_KEYS},
        "updated_by": row.get("updated_by", "") if row else "",
        "updated_at": str(row.get("updated_at") or "") if row else "",
    }


def _trial_row_to_mechanical(row: dict | None) -> dict:
    payload = _trial_row_to_payload(row)
    hardness_1, hardness_2, hardness_3 = _split_hardness(payload["hardness"])
    hardness = "/".join([hardness_1, hardness_2, hardness_3]).strip("/")
    return {
        "test_temp": "RT",
        "yield_strength": payload["yield_strength"],
        "tensile_strength": payload["tensile_strength"],
        "elongation": payload["elongation"],
        "reduction_area": payload["reduction_area"],
        "hardness": hardness,
        "hardness_1": hardness_1,
        "hardness_2": hardness_2,
        "hardness_3": hardness_3,
        "impact_test": payload["impact_test"],
    }


def _json_diff_line(before: dict[str, Any], after: dict[str, Any]) -> str:
    return f"old={json.dumps(before, ensure_ascii=False)} new={json.dumps(after, ensure_ascii=False)}"


TRIAL_CONTENT_FIELDS = (
    "test_date",
    "heat_no",
    "batch_no",
    "spec_model",
    "yield_strength",
    "tensile_strength",
    "elongation",
    "reduction_area",
    "hardness",
    "impact_test",
    "seq_no",
    "supplier_hardness",
    "material_no",
    "remark",
    "material",
)


def _trial_business_key(row: dict) -> tuple[str, str, str, str]:
    if "mech_yield_strength" in row:
        return (
            _normalize_str(row.get("heat_no", "")),
            _normalize_str(row.get("batch_no", "")),
            _normalize_str(row.get("material_no", "")),
            _normalize_str(row.get("spec_model", "")),
        )
    return (
        _normalize_str(row.get("heat_no", "")),
        _normalize_str(row.get("batch_no", "")),
        _normalize_str(row.get("material_no", "")),
        _normalize_str(row.get("spec_model", "")),
    )


def _trial_key_label(key: tuple[str, str, str, str]) -> str:
    heat_no, batch_no, material_no, spec_model = key
    return (
        f"炉号【{heat_no or '/'}】热处理批号【{batch_no or '/'}】"
        f"物料号【{material_no or '/'}】规格型号【{spec_model or '/'}】"
    )


def _trial_content_snapshot(row: dict) -> dict[str, str]:
    if "mech_yield_strength" in row:
        payload = _trial_row_to_payload(row)
    else:
        payload = {
            "test_date": row.get("test_date", "") or "",
            "heat_no": row.get("heat_no", "") or "",
            "batch_no": row.get("batch_no", "") or "",
            "spec_model": row.get("spec_model", "") or "",
            "yield_strength": row.get("yield_strength", "") or "",
            "tensile_strength": row.get("tensile_strength", "") or "",
            "elongation": row.get("elongation", "") or "",
            "reduction_area": row.get("reduction_area", "") or "",
            "hardness": row.get("hardness", "") or "",
            "impact_test": row.get("impact_test", "") or "",
            "seq_no": row.get("seq_no", "") or "",
            "supplier_hardness": row.get("supplier_hardness", "") or "",
            "material_no": row.get("material_no", "") or "",
            "remark": row.get("remark", "") or "",
            "material": row.get("material", "") or "",
        }
    return {field: str(payload.get(field, "") or "") for field in TRIAL_CONTENT_FIELDS}


def _trial_content_equal(row_a: dict, row_b: dict) -> bool:
    return _trial_content_snapshot(row_a) == _trial_content_snapshot(row_b)


def _execute_trial_update(row: dict, row_id: int, updated_by: str, existing_by_id: dict[int, dict]) -> dict:
    hardness_1, hardness_2, hardness_3 = _split_hardness(row["hardness"])
    before = _trial_row_to_payload(existing_by_id.get(row_id))
    after = {**row, "id": row_id, "updated_by": updated_by}
    execute(
        """
        UPDATE heat_treatment_trial_records
        SET test_date=%s, heat_no=%s, batch_no=%s, spec_model=%s,
            mech_yield_strength=%s, mech_tensile_strength=%s, mech_elongation=%s, mech_reduction_area=%s,
            mech_hardness_1=%s, mech_hardness_2=%s, mech_hardness_3=%s, mech_impact_test=%s,
            seq_no=%s, supplier_hardness=%s, material_no=%s, remark=%s, material=%s,
            updated_by=%s, updated_at=NOW()
        WHERE id=%s
        """,
        (
            row["test_date"] or None,
            row["heat_no"],
            row["batch_no"],
            row["spec_model"] or "",
            row["yield_strength"],
            row["tensile_strength"],
            row["elongation"],
            row["reduction_area"],
            hardness_1,
            hardness_2,
            hardness_3,
            row["impact_test"],
            row["seq_no"] or None,
            row["supplier_hardness"] or None,
            row["material_no"] or "",
            row["remark"] or None,
            row["material"] or None,
            updated_by,
            row_id,
        ),
    )
    if before != {**after, "updated_at": before.get("updated_at", "")}:
        _append_log_line(
            f"heat_trial_change_{datetime.now().strftime('%Y-%m-%d')}.log",
            (
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
                f"heat_no={row['heat_no']} batch_no={row['batch_no']} row_id={row_id} "
                f"{_json_diff_line(before, after)}\n"
            ),
        )
    return {**row, "id": row_id, "updated_by": updated_by}


def _execute_trial_insert(row: dict, updated_by: str) -> dict:
    hardness_1, hardness_2, hardness_3 = _split_hardness(row["hardness"])
    inserted_id = execute(
        """
        INSERT INTO heat_treatment_trial_records (
            test_date, heat_no, batch_no, spec_model,
            mech_yield_strength, mech_tensile_strength, mech_elongation, mech_reduction_area,
            mech_hardness_1, mech_hardness_2, mech_hardness_3, mech_impact_test,
            seq_no, supplier_hardness, material_no, remark, material, updated_by
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            row["test_date"] or None,
            row["heat_no"],
            row["batch_no"],
            row["spec_model"] or "",
            row["yield_strength"],
            row["tensile_strength"],
            row["elongation"],
            row["reduction_area"],
            hardness_1,
            hardness_2,
            hardness_3,
            row["impact_test"],
            row["seq_no"] or None,
            row["supplier_hardness"] or None,
            row["material_no"] or "",
            row["remark"] or None,
            row["material"] or None,
            updated_by,
        ),
    )
    return {**row, "id": inserted_id, "updated_by": updated_by}


def _sync_legacy_record(order_no: str, heat_no: str, batch_no: str, updated_by: str, material_no: str = "") -> dict:
    if not _normalize_str(heat_no) or not _normalize_str(batch_no):
        return _empty_record(order_no, heat_no, batch_no, material_no)
    selected_material_no = _normalize_str(material_no)
    _, trial_row = _select_trial_db_row_for_material(heat_no, batch_no, selected_material_no)
    selected_material_no = selected_material_no or _normalize_str((trial_row or {}).get("material_no", ""))
    chemical_row = fetch_one(
        """
        SELECT * FROM heat_treatment_chemical_records
        WHERE heat_no=%s
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (heat_no,),
    )
    mechanical = _trial_row_to_mechanical(trial_row)
    chemical = _chemical_row_to_payload(chemical_row)
    test_result, remark = _evaluate_result(mechanical, chemical)
    chemical_missing = chemical_row is None
    execute(
        """
        INSERT INTO heat_treatment_test_records (
            heat_no, batch_no, material_no,
            mech_test_temp, mech_yield_strength, mech_tensile_strength, mech_elongation,
            mech_reduction_area, mech_hardness, mech_impact_test,
            chem_raw_c, chem_raw_mn, chem_raw_p, chem_raw_s, chem_raw_si, chem_raw_cu, chem_raw_ni, chem_raw_cr, chem_raw_mo, chem_raw_v, chem_raw_ceq,
            chem_self_c, chem_self_mn, chem_self_p, chem_self_s, chem_self_si, chem_self_cu, chem_self_ni, chem_self_cr, chem_self_mo, chem_self_v, chem_self_ceq,
            test_result, remark, created_by
        ) VALUES (
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s
        )
        ON DUPLICATE KEY UPDATE
            mech_test_temp=VALUES(mech_test_temp),
            mech_yield_strength=VALUES(mech_yield_strength),
            mech_tensile_strength=VALUES(mech_tensile_strength),
            mech_elongation=VALUES(mech_elongation),
            mech_reduction_area=VALUES(mech_reduction_area),
            mech_hardness=VALUES(mech_hardness),
            mech_impact_test=VALUES(mech_impact_test),
            chem_raw_c=VALUES(chem_raw_c),
            chem_raw_mn=VALUES(chem_raw_mn),
            chem_raw_p=VALUES(chem_raw_p),
            chem_raw_s=VALUES(chem_raw_s),
            chem_raw_si=VALUES(chem_raw_si),
            chem_raw_cu=VALUES(chem_raw_cu),
            chem_raw_ni=VALUES(chem_raw_ni),
            chem_raw_cr=VALUES(chem_raw_cr),
            chem_raw_mo=VALUES(chem_raw_mo),
            chem_raw_v=VALUES(chem_raw_v),
            chem_raw_ceq=VALUES(chem_raw_ceq),
            chem_self_c=VALUES(chem_self_c),
            chem_self_mn=VALUES(chem_self_mn),
            chem_self_p=VALUES(chem_self_p),
            chem_self_s=VALUES(chem_self_s),
            chem_self_si=VALUES(chem_self_si),
            chem_self_cu=VALUES(chem_self_cu),
            chem_self_ni=VALUES(chem_self_ni),
            chem_self_cr=VALUES(chem_self_cr),
            chem_self_mo=VALUES(chem_self_mo),
            chem_self_v=VALUES(chem_self_v),
            chem_self_ceq=VALUES(chem_self_ceq),
            test_result=VALUES(test_result),
            remark=VALUES(remark),
            created_by=VALUES(created_by)
        """,
        (
            heat_no,
            batch_no,
            selected_material_no,
            mechanical["test_temp"],
            mechanical["yield_strength"] or None,
            mechanical["tensile_strength"] or None,
            mechanical["elongation"] or None,
            mechanical["reduction_area"] or None,
            mechanical["hardness"] or None,
            mechanical["impact_test"] or None,
            _round_three(chemical["raw"].get("C")),
            _round_three(chemical["raw"].get("Mn")),
            _round_three(chemical["raw"].get("P")),
            _round_three(chemical["raw"].get("S")),
            _round_three(chemical["raw"].get("Si")),
            _round_three(chemical["raw"].get("Cu")),
            _round_three(chemical["raw"].get("Ni")),
            _round_three(chemical["raw"].get("Cr")),
            _round_three(chemical["raw"].get("Mo")),
            _round_three(chemical["raw"].get("V")),
            _round_three(chemical["raw"].get("CEQ")),
            _round_three(chemical["self"].get("C")),
            _round_three(chemical["self"].get("Mn")),
            _round_three(chemical["self"].get("P")),
            _round_three(chemical["self"].get("S")),
            _round_three(chemical["self"].get("Si")),
            _round_three(chemical["self"].get("Cu")),
            _round_three(chemical["self"].get("Ni")),
            _round_three(chemical["self"].get("Cr")),
            _round_three(chemical["self"].get("Mo")),
            _round_three(chemical["self"].get("V")),
            _round_three(chemical["self"].get("CEQ")),
            test_result or None,
            remark or None,
            updated_by or None,
        ),
    )
    row = fetch_one(
        """
        SELECT * FROM heat_treatment_test_records
        WHERE heat_no=%s AND batch_no=%s AND material_no=%s
        """,
        (heat_no, batch_no, selected_material_no),
    )
    record = _legacy_row_to_record(row, order_no, heat_no, batch_no, selected_material_no)
    record["chemical_missing"] = chemical_missing
    heat_treatment_test_records[_key(heat_no, batch_no, selected_material_no)] = record
    return record


def _normalize_trial_row(row: dict, fallback_heat_no: str, fallback_batch_no: str) -> dict:
    normalized = {
        "id": row.get("id"),
        "test_date": _normalize_str(row.get("test_date", "")),
        "heat_no": _normalize_str(row.get("heat_no", "")) or fallback_heat_no,
        "batch_no": _normalize_str(row.get("batch_no", "")) or fallback_batch_no,
        "spec_model": _normalize_str(row.get("spec_model", "")),
        "yield_strength": _normalize_str(row.get("yield_strength", "")),
        "tensile_strength": _normalize_str(row.get("tensile_strength", "")),
        "elongation": _normalize_str(row.get("elongation", "")),
        "reduction_area": _normalize_str(row.get("reduction_area", "")),
        "hardness": _normalize_str(row.get("hardness", "")),
        "impact_test": _normalize_str(row.get("impact_test", "")),
        "seq_no": _normalize_str(row.get("seq_no", "")),
        "supplier_hardness": _normalize_str(row.get("supplier_hardness", "")),
        "material_no": _normalize_str(row.get("material_no", "")),
        "remark": _normalize_str(row.get("remark", "")),
        "material": _normalize_str(row.get("material", "")),
    }
    if normalized["test_date"] and (not normalized["test_date"].isdigit() or len(normalized["test_date"]) != 8):
        raise HTTPException(status_code=400, detail="日期必须为yyyymmdd格式或留空")
    required_fields = [
        ("heat_no", "炉号"),
        ("batch_no", "热处理批号"),
        ("yield_strength", "屈服标准"),
        ("tensile_strength", "抗拉标准"),
        ("elongation", "延伸标准"),
        ("reduction_area", "Z标准"),
        ("hardness", "硬度1/硬度2/硬度3"),
        ("impact_test", "-20℃下冲击标准"),
    ]
    for key, label in required_fields:
        if not normalized[key]:
            raise HTTPException(status_code=400, detail=f"{label}不能为空")
    for key, label in (
        ("yield_strength", "屈服标准"),
        ("tensile_strength", "抗拉标准"),
        ("elongation", "延伸标准"),
        ("reduction_area", "Z标准"),
    ):
        _parse_number(normalized[key], label)
    _validate_impact_test(normalized["impact_test"])
    return normalized


class HeatTreatmentQuery(BaseModel):
    order_no: str = ""
    heat_no: str
    batch_no: str


class HeatTreatmentSave(BaseModel):
    order_no: str
    heat_no: str
    batch_no: str
    mechanical: dict
    chemical: dict
    created_by: str = ""


class TrialQueryRequest(BaseModel):
    heat_no: str = ""
    batch_no: str = ""


class TrialSaveRequest(BaseModel):
    order_no: str = ""
    heat_no: str = ""
    batch_no: str = ""
    # 兼容旧客户端；实际更新者以后端登录用户为准
    updated_by: str = ""
    rows: list[dict] = Field(default_factory=list)
    confirm_update_indices: list[int] = Field(default_factory=list)


class TrialDeleteRequest(BaseModel):
    id: int


class ChemicalQueryRequest(BaseModel):
    heat_no: str = ""


class ChemicalSaveRequest(BaseModel):
    order_no: str = ""
    heat_no: str = ""
    batch_no: str = ""
    # 兼容旧客户端；实际更新者以后端登录用户为准
    updated_by: str = ""
    raw: dict = Field(default_factory=dict)
    self: dict = Field(default_factory=dict)


class ResultQueryRequest(BaseModel):
    order_no: str = ""
    heat_no: str = ""
    batch_no: str = ""
    material_no: str = ""


class ResultSubmitRequest(BaseModel):
    order_no: str = ""
    heat_no: str = ""
    batch_no: str = ""
    material_no: str = ""
    updated_by: str = ""


class ResultMaterialQueryRequest(BaseModel):
    order_no: str = ""
    heat_no: str = ""
    batch_no: str = ""
    material_no: str = ""


class ResultSyncRowRequest(BaseModel):
    order_no: str = ""
    heat_no: str = ""
    batch_no: str = ""
    material_no: str = ""
    updated_by: str = ""


def _spec_model_numeric_prefix(spec_model: str) -> float | None:
    """取 spec_model 半角空格前段的 leading number，如 '318 DN600' → 318.0。"""
    prefix = _normalize_str(spec_model).split()[0] if _normalize_str(spec_model) else ""
    match = re.match(r"^(\d+(?:\.\d+)?)", prefix)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _select_trial_min_max_rows(heat_no: str, batch_no: str) -> tuple[list[dict], dict | None, dict | None]:
    """大连质检：按 spec_model 前缀数字取 min/max 两组试验记录。

    仅 1 条记录时只返回 min（第一行），max 为 None（第二行留空）。
    """
    hn = _normalize_str(heat_no)
    bn = _normalize_str(batch_no)
    rows = fetch_all(
        """
        SELECT * FROM heat_treatment_trial_records
        WHERE heat_no=%s AND batch_no=%s
        ORDER BY id
        """,
        (hn, bn),
    )
    if not rows:
        return rows, None, None
    if len(rows) == 1:
        return rows, rows[0], None

    def sort_key(row: dict) -> tuple[float, int]:
        prefix = _spec_model_numeric_prefix(row.get("spec_model", ""))
        row_id = int(row.get("id") or 0)
        return (prefix if prefix is not None else float("inf"), row_id)

    sorted_rows = sorted(rows, key=sort_key)
    return rows, sorted_rows[0], sorted_rows[-1]


def _select_trial_db_row_for_material(heat_no: str, batch_no: str, material_no: str) -> tuple[list[dict], dict | None]:
    """质检/测试结果选型：0 条 None；1 条用之；多条优先 material_no 相同否则 ORDER BY id 的最后一条。"""
    hn = _normalize_str(heat_no)
    bn = _normalize_str(batch_no)
    mn = _normalize_str(material_no)
    rows = fetch_all(
        """
        SELECT * FROM heat_treatment_trial_records
        WHERE heat_no=%s AND batch_no=%s
        ORDER BY id
        """,
        (hn, bn),
    )
    if not rows:
        return rows, None
    if len(rows) == 1:
        return rows, rows[0]
    for row in reversed(rows):
        if _normalize_str(row.get("material_no", "")) == mn:
            return rows, row
    return rows, rows[-1]


@router.post("/trial/query")
def query_trial_records(req: TrialQueryRequest):
    rows = fetch_all(
        """
        SELECT * FROM heat_treatment_trial_records
        WHERE heat_no=%s AND batch_no=%s
        ORDER BY id
        """,
        (req.heat_no, req.batch_no),
    )
    return {"status": "success", "rows": [_trial_row_to_payload(row) for row in rows], "exists": bool(rows)}


@router.post("/trial/save")
def save_trial_records(
    req: TrialSaveRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    updated_by = _operator_name(user)
    target_heat_no = _normalize_str(req.heat_no) or next((_normalize_str(row.get("heat_no", "")) for row in req.rows if _normalize_str(row.get("heat_no", ""))), "")
    target_batch_no = _normalize_str(req.batch_no) or next((_normalize_str(row.get("batch_no", "")) for row in req.rows if _normalize_str(row.get("batch_no", ""))), "")
    if not target_heat_no or not target_batch_no:
        raise HTTPException(status_code=400, detail="炉号/热处理批号不能为空")

    normalized_rows: list[dict] = []
    for raw_row in req.rows:
        normalized_rows.append(_normalize_trial_row(raw_row, target_heat_no, target_batch_no))

    seen_keys: dict[tuple[str, str, str, str], int] = {}
    for idx, row in enumerate(normalized_rows):
        key = _trial_business_key(row)
        if key in seen_keys:
            label = _trial_key_label(key)
            raise HTTPException(status_code=400, detail=f"提交数据中存在重复记录：{label}")
        seen_keys[key] = idx

    existing_rows = fetch_all(
        """
        SELECT * FROM heat_treatment_trial_records
        WHERE heat_no=%s AND batch_no=%s
        ORDER BY id
        """,
        (target_heat_no, target_batch_no),
    )
    existing_by_id: dict[int, dict] = {
        int(row["id"]): row for row in existing_rows if row.get("id") is not None
    }
    existing_by_key: dict[tuple[str, str, str, str], dict] = {
        _trial_business_key(row): row for row in existing_rows
    }

    for row in normalized_rows:
        row_id = int(row["id"]) if row.get("id") else None
        if row_id and row_id not in existing_by_id:
            db_row = fetch_one("SELECT * FROM heat_treatment_trial_records WHERE id=%s LIMIT 1", (row_id,))
            if db_row:
                existing_by_id[row_id] = db_row
                key = _trial_business_key(db_row)
                if key not in existing_by_key:
                    existing_by_key[key] = db_row

    confirm_set = set(req.confirm_update_indices)
    id_mismatch_indexes: list[int] = []
    unchanged_items: list[dict] = []
    confirm_items: list[dict] = []
    planned_actions: list[tuple[str, dict, int | None, int]] = []

    for idx, row in enumerate(normalized_rows):
        key = _trial_business_key(row)
        key_label = _trial_key_label(key)
        row_id = int(row["id"]) if row.get("id") else None
        existing_for_key = existing_by_key.get(key)

        if row_id:
            db_row = existing_by_id.get(row_id)
            if db_row and _trial_business_key(db_row) == key:
                planned_actions.append(("update", row, row_id, idx))
                continue
            if db_row and _trial_business_key(db_row) != key:
                id_mismatch_indexes.append(idx)
                continue
            if existing_for_key:
                if _trial_content_equal(row, existing_for_key):
                    unchanged_items.append({"index": idx, "label": key_label})
                elif idx not in confirm_set:
                    confirm_items.append(
                        {
                            "index": idx,
                            "label": key_label,
                            "existing_id": int(existing_for_key["id"]),
                        }
                    )
                else:
                    planned_actions.append(("update", row, int(existing_for_key["id"]), idx))
                continue
            planned_actions.append(("insert", row, None, idx))
            continue

        if existing_for_key:
            if _trial_content_equal(row, existing_for_key):
                unchanged_items.append({"index": idx, "label": key_label})
            elif idx not in confirm_set:
                confirm_items.append(
                    {
                        "index": idx,
                        "label": key_label,
                        "existing_id": int(existing_for_key["id"]),
                    }
                )
            else:
                planned_actions.append(("update", row, int(existing_for_key["id"]), idx))
            continue

        planned_actions.append(("insert", row, None, idx))

    if id_mismatch_indexes:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "id重复，请删除该行信息确认后重新提交",
                "code": "id_mismatch",
                "row_indexes": id_mismatch_indexes,
            },
        )

    own_ids: dict[int, int | None] = {}
    for _action, _row, row_id, source_index in planned_actions:
        own_ids[source_index] = row_id
    for item in unchanged_items:
        existing = existing_by_key.get(_trial_business_key(normalized_rows[item["index"]]))
        own_ids[item["index"]] = int(existing["id"]) if existing and existing.get("id") is not None else None
    for item in confirm_items:
        own_ids[item["index"]] = int(item["existing_id"])
    if own_ids:
        all_value_rows = fetch_all(
            """
            SELECT id, mech_hardness_1, mech_hardness_2, mech_hardness_3, mech_impact_test
            FROM heat_treatment_trial_records
            """
        )
        _raise_duplicate_trial_values(
            _collect_duplicate_trial_value_errors(normalized_rows, own_ids, all_value_rows)
        )

    if unchanged_items:
        labels = "、".join(item["label"] for item in unchanged_items)
        return {
            "status": "unchanged",
            "message": f"已存在 {labels} 的数据，无数据更新",
            "unchanged": unchanged_items,
        }

    if confirm_items:
        labels = "、".join(item["label"] for item in confirm_items)
        return {
            "status": "confirm_required",
            "message": f"已存在 {labels} 的数据，是否更新该条数据的试验记录？",
            "conflicts": confirm_items,
        }

    saved_rows: list[dict] = []
    for action, row, row_id, source_index in planned_actions:
        if action == "update" and row_id is not None:
            saved = _execute_trial_update(row, row_id, updated_by, existing_by_id)
        elif action == "insert":
            saved = _execute_trial_insert(row, updated_by)
        else:
            continue
        saved_rows.append({**saved, "index": source_index})

    return {
        "status": "success",
        "saved_count": len(saved_rows),
        "rows": saved_rows,
    }


@router.post("/trial/delete")
def delete_trial_record(req: TrialDeleteRequest):
    existing = fetch_one("SELECT * FROM heat_treatment_trial_records WHERE id=%s LIMIT 1", (req.id,))
    if not existing:
        raise HTTPException(status_code=404, detail="试验记录不存在或已被删除")
    execute("DELETE FROM heat_treatment_trial_records WHERE id=%s", (req.id,))
    _append_log_line(
        f"heat_trial_change_{datetime.now().strftime('%Y-%m-%d')}.log",
        (
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
            f"heat_no={existing.get('heat_no', '')} batch_no={existing.get('batch_no', '')} row_id={req.id} "
            f"deleted={json.dumps(_trial_row_to_payload(existing), ensure_ascii=False)}\n"
        ),
    )
    return {"status": "success", "deleted_id": req.id}


@router.post("/chemical/query")
def query_chemical_record(req: ChemicalQueryRequest):
    row = fetch_one(
        """
        SELECT * FROM heat_treatment_chemical_records
        WHERE heat_no=%s
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (req.heat_no,),
    )
    return {"status": "success", "record": _chemical_row_to_payload(row), "exists": bool(row)}


@router.post("/chemical/save")
def save_chemical_record(
    req: ChemicalSaveRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    updated_by = _operator_name(user)
    if not _normalize_str(req.heat_no):
        raise HTTPException(status_code=400, detail="炉号不能为空")
    normalized_raw = {key: _round_three(req.raw.get(key, "")) for key in CHEM_KEYS if key != "CEQ"}
    normalized_self = {key: _round_three(req.self.get(key, "")) for key in CHEM_KEYS if key != "CEQ"}
    normalized_raw["CEQ"] = _calculate_ceq(normalized_raw)
    normalized_self["CEQ"] = _calculate_ceq(normalized_self)
    existing = fetch_one("SELECT * FROM heat_treatment_chemical_records WHERE heat_no=%s LIMIT 1", (req.heat_no,))
    if existing:
        before = _chemical_row_to_payload(existing)
        execute(
            """
            UPDATE heat_treatment_chemical_records
            SET chem_raw_c=%s, chem_raw_mn=%s, chem_raw_p=%s, chem_raw_s=%s, chem_raw_si=%s, chem_raw_cu=%s, chem_raw_ni=%s,
                chem_raw_cr=%s, chem_raw_mo=%s, chem_raw_v=%s, chem_raw_ceq=%s,
                chem_self_c=%s, chem_self_mn=%s, chem_self_p=%s, chem_self_s=%s, chem_self_si=%s, chem_self_cu=%s, chem_self_ni=%s,
                chem_self_cr=%s, chem_self_mo=%s, chem_self_v=%s, chem_self_ceq=%s,
                updated_by=%s, updated_at=NOW()
            WHERE id=%s
            """,
            (
                normalized_raw["C"],
                normalized_raw["Mn"],
                normalized_raw["P"],
                normalized_raw["S"],
                normalized_raw["Si"],
                normalized_raw["Cu"],
                normalized_raw["Ni"],
                normalized_raw["Cr"],
                normalized_raw["Mo"],
                normalized_raw["V"],
                normalized_raw["CEQ"],
                normalized_self["C"],
                normalized_self["Mn"],
                normalized_self["P"],
                normalized_self["S"],
                normalized_self["Si"],
                normalized_self["Cu"],
                normalized_self["Ni"],
                normalized_self["Cr"],
                normalized_self["Mo"],
                normalized_self["V"],
                normalized_self["CEQ"],
                updated_by,
                int(existing["id"]),
            ),
        )
        after = {
            "heat_no": req.heat_no,
            "raw": {key: "" if normalized_raw[key] is None else normalized_raw[key] for key in CHEM_KEYS},
            "self": {key: "" if normalized_self[key] is None else normalized_self[key] for key in CHEM_KEYS},
            "updated_by": updated_by,
        }
        _append_log_line(
            f"heat_chemical_change_{datetime.now().strftime('%Y-%m-%d')}.log",
            (
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
                f"heat_no={req.heat_no} {_json_diff_line(before, after)}\n"
            ),
        )
    else:
        execute(
            """
            INSERT INTO heat_treatment_chemical_records (
                heat_no,
                chem_raw_c, chem_raw_mn, chem_raw_p, chem_raw_s, chem_raw_si, chem_raw_cu, chem_raw_ni, chem_raw_cr, chem_raw_mo, chem_raw_v, chem_raw_ceq,
                chem_self_c, chem_self_mn, chem_self_p, chem_self_s, chem_self_si, chem_self_cu, chem_self_ni, chem_self_cr, chem_self_mo, chem_self_v, chem_self_ceq,
                updated_by
            ) VALUES (
                %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s
            )
            """,
            (
                req.heat_no,
                normalized_raw["C"],
                normalized_raw["Mn"],
                normalized_raw["P"],
                normalized_raw["S"],
                normalized_raw["Si"],
                normalized_raw["Cu"],
                normalized_raw["Ni"],
                normalized_raw["Cr"],
                normalized_raw["Mo"],
                normalized_raw["V"],
                normalized_raw["CEQ"],
                normalized_self["C"],
                normalized_self["Mn"],
                normalized_self["P"],
                normalized_self["S"],
                normalized_self["Si"],
                normalized_self["Cu"],
                normalized_self["Ni"],
                normalized_self["Cr"],
                normalized_self["Mo"],
                normalized_self["V"],
                normalized_self["CEQ"],
                updated_by,
            ),
        )
    legacy_record = _sync_legacy_record(req.order_no, req.heat_no, req.batch_no, updated_by)
    row = fetch_one(
        """
        SELECT * FROM heat_treatment_chemical_records
        WHERE heat_no=%s
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (req.heat_no,),
    )
    return {"status": "success", "record": _chemical_row_to_payload(row), "result_record": legacy_record}


@router.post("/result/query")
def query_result(req: ResultQueryRequest):
    _, trial_row = _select_trial_db_row_for_material(req.heat_no, req.batch_no, req.material_no)
    selected_material_no = _normalize_str(req.material_no) or _normalize_str((trial_row or {}).get("material_no", ""))
    result_row = fetch_one(
        """
        SELECT * FROM heat_treatment_test_records
        WHERE heat_no=%s AND batch_no=%s
        ORDER BY CASE WHEN material_no=%s THEN 0 ELSE 1 END, updated_at DESC, id DESC
        LIMIT 1
        """,
        (req.heat_no, req.batch_no, selected_material_no),
    )
    if not result_row:
        raise HTTPException(status_code=404, detail=f"炉号【{req.heat_no}】热处理号【{req.batch_no}】无测试结果")
    chemical_row = fetch_one(
        """
        SELECT * FROM heat_treatment_chemical_records
        WHERE heat_no=%s
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (req.heat_no,),
    )
    record = _legacy_row_to_record(result_row, req.order_no, req.heat_no, req.batch_no, selected_material_no)
    mechanical = record["mechanical"]
    chemical = _chemical_row_to_payload(chemical_row)
    return {
        "status": "success",
        "trial_row": _trial_row_to_payload(trial_row),
        "chemical_record": chemical,
        "mechanical": mechanical,
        "chemical": record["chemical"],
        "test_result": record["test_result"],
        "remark": record["remark"],
        "record": record,
    }


@router.post("/result/submit")
def submit_result(
    req: ResultSubmitRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    record = _sync_legacy_record(req.order_no, req.heat_no, req.batch_no, _operator_name(user), req.material_no)
    return {"status": "success", "record": record}


@router.post("/result/material-query")
def query_result_for_material_row(req: ResultMaterialQueryRequest):
    hn = _normalize_str(req.heat_no)
    bn = _normalize_str(req.batch_no)
    if not hn or not bn:
        raise HTTPException(status_code=400, detail="炉号和热处理批号不能为空")
    trial_rows, trial_db = _select_trial_db_row_for_material(hn, bn, req.material_no)
    mn = _normalize_str(req.material_no) or _normalize_str((trial_db or {}).get("material_no", ""))
    result_row = fetch_one(
        """
        SELECT * FROM heat_treatment_test_records
        WHERE heat_no=%s AND batch_no=%s
        ORDER BY CASE WHEN material_no=%s THEN 0 ELSE 1 END, updated_at DESC, id DESC
        LIMIT 1
        """,
        (hn, bn, mn),
    )
    if not result_row:
        raise HTTPException(status_code=404, detail=f"炉号【{hn}】热处理号【{bn}】无测试结果")
    chemical_row = fetch_one(
        """
        SELECT * FROM heat_treatment_chemical_records
        WHERE heat_no=%s
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (hn,),
    )
    record = _legacy_row_to_record(result_row, req.order_no, hn, bn, mn)
    chemical = _chemical_row_to_payload(chemical_row)
    return {
        "status": "success",
        "trial_rows_count": len(trial_rows),
        "trial_row": _trial_row_to_payload(trial_db),
        "chemical_record": chemical,
        "mechanical": record["mechanical"],
        "chemical": record["chemical"],
        "test_result": record["test_result"],
        "remark": record["remark"],
        "record": record,
    }


@router.post("/result/sync-row")
def sync_result_row(
    req: ResultSyncRowRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    hn = _normalize_str(req.heat_no)
    bn = _normalize_str(req.batch_no)
    if not hn or not bn:
        raise HTTPException(status_code=400, detail="炉号和热处理批号不能为空")
    record = _sync_legacy_record(req.order_no, hn, bn, _operator_name(user), req.material_no)
    return {"status": "success", "record": record, "chemical_missing": bool(record.get("chemical_missing"))}


@router.post("/query-or-init")
def query_or_init(req: HeatTreatmentQuery):
    row = fetch_one(
        """
        SELECT * FROM heat_treatment_test_records
        WHERE heat_no=%s AND batch_no=%s
        """,
        (req.heat_no, req.batch_no),
    )
    mode = "edit" if row else "new"
    record = _legacy_row_to_record(row, req.order_no, req.heat_no, req.batch_no)
    if row:
        heat_treatment_test_records[_key(req.heat_no, req.batch_no)] = record
    return {"status": "success", "mode": mode, "record": record}


@router.post("/save")
def save_record(req: HeatTreatmentSave):
    if not req.heat_no or not req.batch_no:
        raise HTTPException(status_code=400, detail="炉号/批号不能为空")
    execute(
        """
        INSERT INTO heat_treatment_test_records (
            heat_no, batch_no,
            mech_test_temp, mech_yield_strength, mech_tensile_strength, mech_elongation,
            mech_reduction_area, mech_hardness, mech_impact_test,
            chem_raw_c, chem_raw_mn, chem_raw_p, chem_raw_s, chem_raw_si, chem_raw_cu, chem_raw_ni, chem_raw_cr, chem_raw_mo, chem_raw_v, chem_raw_ceq,
            chem_self_c, chem_self_mn, chem_self_p, chem_self_s, chem_self_si, chem_self_cu, chem_self_ni, chem_self_cr, chem_self_mo, chem_self_v, chem_self_ceq,
            test_result, remark, created_by
        ) VALUES (
            %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s
        )
        ON DUPLICATE KEY UPDATE
            mech_test_temp=VALUES(mech_test_temp),
            mech_yield_strength=VALUES(mech_yield_strength),
            mech_tensile_strength=VALUES(mech_tensile_strength),
            mech_elongation=VALUES(mech_elongation),
            mech_reduction_area=VALUES(mech_reduction_area),
            mech_hardness=VALUES(mech_hardness),
            mech_impact_test=VALUES(mech_impact_test),
            chem_raw_c=VALUES(chem_raw_c),
            chem_raw_mn=VALUES(chem_raw_mn),
            chem_raw_p=VALUES(chem_raw_p),
            chem_raw_s=VALUES(chem_raw_s),
            chem_raw_si=VALUES(chem_raw_si),
            chem_raw_cu=VALUES(chem_raw_cu),
            chem_raw_ni=VALUES(chem_raw_ni),
            chem_raw_cr=VALUES(chem_raw_cr),
            chem_raw_mo=VALUES(chem_raw_mo),
            chem_raw_v=VALUES(chem_raw_v),
            chem_raw_ceq=VALUES(chem_raw_ceq),
            chem_self_c=VALUES(chem_self_c),
            chem_self_mn=VALUES(chem_self_mn),
            chem_self_p=VALUES(chem_self_p),
            chem_self_s=VALUES(chem_self_s),
            chem_self_si=VALUES(chem_self_si),
            chem_self_cu=VALUES(chem_self_cu),
            chem_self_ni=VALUES(chem_self_ni),
            chem_self_cr=VALUES(chem_self_cr),
            chem_self_mo=VALUES(chem_self_mo),
            chem_self_v=VALUES(chem_self_v),
            chem_self_ceq=VALUES(chem_self_ceq),
            test_result=VALUES(test_result),
            remark=VALUES(remark),
            created_by=VALUES(created_by)
        """,
        (
            req.heat_no,
            req.batch_no,
            "RT",
            _normalize_str(req.mechanical.get("yield_strength", "")) or None,
            _normalize_str(req.mechanical.get("tensile_strength", "")) or None,
            _normalize_str(req.mechanical.get("elongation", "")) or None,
            _normalize_str(req.mechanical.get("reduction_area", "")) or None,
            "/".join(
                [
                    _normalize_str(req.mechanical.get("hardness_1", "")),
                    _normalize_str(req.mechanical.get("hardness_2", "")),
                    _normalize_str(req.mechanical.get("hardness_3", "")),
                ]
            ).strip("/") or None,
            _normalize_str(req.mechanical.get("impact_test", "")) or None,
            _round_three(req.chemical.get("raw", {}).get("C")),
            _round_three(req.chemical.get("raw", {}).get("Mn")),
            _round_three(req.chemical.get("raw", {}).get("P")),
            _round_three(req.chemical.get("raw", {}).get("S")),
            _round_three(req.chemical.get("raw", {}).get("Si")),
            _round_three(req.chemical.get("raw", {}).get("Cu")),
            _round_three(req.chemical.get("raw", {}).get("Ni")),
            _round_three(req.chemical.get("raw", {}).get("Cr")),
            _round_three(req.chemical.get("raw", {}).get("Mo")),
            _round_three(req.chemical.get("raw", {}).get("V")),
            _round_three(req.chemical.get("raw", {}).get("CEQ")),
            _round_three(req.chemical.get("self", {}).get("C")),
            _round_three(req.chemical.get("self", {}).get("Mn")),
            _round_three(req.chemical.get("self", {}).get("P")),
            _round_three(req.chemical.get("self", {}).get("S")),
            _round_three(req.chemical.get("self", {}).get("Si")),
            _round_three(req.chemical.get("self", {}).get("Cu")),
            _round_three(req.chemical.get("self", {}).get("Ni")),
            _round_three(req.chemical.get("self", {}).get("Cr")),
            _round_three(req.chemical.get("self", {}).get("Mo")),
            _round_three(req.chemical.get("self", {}).get("V")),
            _round_three(req.chemical.get("self", {}).get("CEQ")),
            None,
            None,
            req.created_by or None,
        ),
    )
    row = fetch_one(
        "SELECT * FROM heat_treatment_test_records WHERE heat_no=%s AND batch_no=%s",
        (req.heat_no, req.batch_no),
    )
    record = _legacy_row_to_record(row, req.order_no, req.heat_no, req.batch_no)
    heat_treatment_test_records[_key(req.heat_no, req.batch_no)] = record
    return {"status": "success", "record": record}
