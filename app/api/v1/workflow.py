"""合同->订单->生产->质检->包装发货的一体化流程API。"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import traceback
from copy import copy
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Border, Font, Side
except Exception:  # pragma: no cover
    Workbook = None
    load_workbook = None
    Alignment = None
    Border = None
    Font = None
    Side = None

try:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt
except Exception:  # pragma: no cover
    Document = None
    WD_ALIGN_PARAGRAPH = None
    qn = None
    Cm = None
    Pt = None

from app.core.database import execute, execute_many, fetch_all, fetch_one, get_db
from app.services.dalian_certificate_allocation import (
    allocate_dalian_certificate,
    normalize_item_no_key,
    parse_certificate_no,
    update_dalian_certificate_allocation,
    yymmdd_from_date_text,
)
from app.services.in_memory_store import (
    heat_treatment_test_records,
    MATERIAL_MODES,
    ORDER_STATUSES,
    ORDER_UPLOAD_TYPES,
    materials,
    now_iso,
    now_local_compact,
    order_detail_seq,
    order_details,
    packing_detail_seq,
    packing_details,
    packing_sessions,
    quality_record_seq,
    quality_records,
)
from app.services.docx_to_pdf import convert_docx_to_pdf
from app.services.pdf_form_renderer import fill_pdf_form
from app.services.docx_page_fit import fit_qc_certificate_to_single_page
from app.services.template_renderer import render_docx_template, render_xlsx_template
from app.services.qc_placeholder_mapping import build_certificate_mapping, cert_same_as_a105
from app.services.qc_certificate_snapshot import (
    enrich_order_detail_with_snapshot,
    get_snapshot_by_id,
    query_snapshots,
    upsert_snapshot_from_record,
)
from app.api.v1 import heat_treatment as heat_treatment_api
from app.core.config import get_settings
from app.core.file_log import append_log_line
from app.core.paths import get_storage_dir, get_templates_dir

router = APIRouter(prefix="/workflow", tags=["流程实装"])

_STORAGE_DIR = get_storage_dir()
OUTPUT_DIR = _STORAGE_DIR / "generated"
TEMPLATES_DIR = get_templates_dir()
LOG_DIR = _STORAGE_DIR / "logs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

_MEDIA_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_MEDIA_PDF = "application/pdf"
_MEDIA_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_QC_TEMPLATE_FILENAME = "quality_certificate.docx"
_QC_PDF_TEMPLATE_FILENAME = "quality_certificate.pdf"

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

MECHANICAL_LIMITS = {
    "yield_strength": ("屈服强度", ">=", 250.0),
    "tensile_strength": ("抗拉强度", ">=", 485.0),
    "elongation": ("伸长率", ">=", 22.0),
    "reduction_area": ("断面收缩率", ">=", 30.0),
}


def _safe_int(v: Any, default: int = 0) -> int:
    if v is None or str(v).strip() == "":
        return default
    return int(float(v))


def _safe_float(v: Any, default: float = 0.0) -> float:
    if v is None or str(v).strip() == "":
        return default
    return round(float(v), 2)


def _normalize_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _append_log_line(file_name: str, line: str) -> None:
    append_log_line(LOG_DIR, file_name, line)


def _qc_exception_message(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, str):
            message = detail
        else:
            message = json.dumps(detail, ensure_ascii=False, default=str)
        return message, f"HTTPException({exc.status_code})"
    return str(exc), type(exc).__name__


def _log_qc_certificate_failure(
    certificate_kind: str,
    record_id: int | None,
    exc: Exception,
    *,
    context: dict[str, Any] | None = None,
) -> str:
    """质保书生成失败：写入日志文件并输出到终端，便于后期维护排查。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tb = traceback.format_exc()
    error_message, error_type = _qc_exception_message(exc)
    log_name = f"qc_certificate_{certificate_kind}_{datetime.now().strftime('%Y-%m-%d')}.log"
    log_path = LOG_DIR / log_name
    payload = {
        "time": ts,
        "certificate_kind": certificate_kind,
        "record_id": record_id,
        "error": error_message,
        "error_type": error_type,
        "context": context or {},
        "traceback": tb,
        "log_file": str(log_path),
    }
    _append_log_line(log_name, json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    terminal_msg = (
        f"\n[质保书生成失败][{certificate_kind}] {ts}\n"
        f"  record_id: {record_id}\n"
        f"  error: {error_message}\n"
        f"  log_file: {log_path}\n"
        f"{tb}"
    )
    try:
        print(terminal_msg, flush=True)
    except UnicodeEncodeError:
        print(
            terminal_msg.encode(sys.stdout.encoding or "utf-8", errors="backslashreplace").decode(
                sys.stdout.encoding or "utf-8", errors="ignore"
            ),
            flush=True,
        )
    return str(log_path)


def _make_temp_output_path(suffix: str) -> Path:
    fd, name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return Path(name)


def _file_download_response(path: Path, download_name: str, media_type: str) -> FileResponse:
    def _unlink() -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    return FileResponse(
        str(path),
        media_type=media_type,
        filename=download_name,
        background=BackgroundTask(_unlink),
    )


def _dedupe_file_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 1
    while True:
        candidate = parent / f"{stem}（{index}）{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


@router.post("/generated-files/save")
async def save_generated_file_to_path(
    file: UploadFile = File(...),
    file_name: str = Form(""),
    directory_path: str = Form(""),
):
    safe_name = Path(_normalize_str(file_name) or _normalize_str(file.filename) or "download.bin").name
    target_dir_text = _normalize_str(directory_path)
    target_dir = Path(target_dir_text) if target_dir_text else Path.home() / "Desktop"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = _dedupe_file_path(target_dir / safe_name)
        target_path.write_bytes(await file.read())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"文件保存失败: {exc}") from exc
    return {"status": "success", "path": str(target_path)}


def _format_three_decimals(v: Any) -> str:
    if v in (None, ""):
        return ""
    try:
        return f"{float(v):.3f}"
    except (TypeError, ValueError):
        return str(v)


def _rounded_or_blank(v: Any) -> float | str:
    if v in (None, ""):
        return ""
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return _normalize_str(v)


def _spec_to_certificate_display(spec: str) -> str:
    raw = _normalize_str(spec)
    if not raw:
        return ""
    return re.sub(r"\b(\d+)A\b", lambda m: f"DN{m.group(1)}", raw, flags=re.IGNORECASE)


def _find_order(order_no: str) -> list[dict]:
    _ensure_order_details_loaded()
    return [r for r in order_details if r["order_no"] == order_no]


def _get_factory_order_no(order_no: str) -> str:
    rows = _find_order(order_no)
    if not rows:
        return ""
    return _normalize_str(rows[0].get("factory_order_no", ""))


def _find_packing_rows(order_no: str) -> list[dict]:
    _ensure_packing_details_loaded()
    return [r for r in packing_details if r["order_no"] == order_no]


def _packing_row_from_db(row: dict) -> dict:
    return {
        "id": row["id"],
        "order_no": row["order_no"],
        "material_no": row["material_no"],
        "spec": row.get("spec", "") or "",
        "standard": row.get("standard", "") or "",
        "material": row.get("material", "") or "",
        "quantity": int(row.get("quantity", 0) or 0),
        "unit_weight": float(row.get("unit_weight", 0) or 0),
        "total_weight": float(row.get("total_weight", 0) or 0),
        "gross_weight": float(row.get("gross_weight", 0) or 0),
        "remark1": row.get("remark1", "") or "",
        "box_no": int(row.get("box_no", 0) or 0),
        "entry_no": int(row["entry_no"]) if row.get("entry_no") is not None else None,
        "box_length": int(row.get("box_length", 0) or 0),
        "box_width": int(row.get("box_width", 0) or 0),
        "box_height": int(row.get("box_height", 0) or 0),
        "packing_remark": row.get("packing_remark", "") or "",
        "delivery_date": str(row.get("delivery_date") or ""),
        "packed_at": str(row.get("packed_at") or ""),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
    }


def _serialize_packing_row(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "source_order_detail_id": row.get("source_order_detail_id"),
        "material_no": row.get("material_no", ""),
        "item_no": row.get("entry_no"),
        "quantity": row.get("quantity", 0),
        "unit_weight": row.get("unit_weight", 0),
        "total_weight": row.get("total_weight", 0),
        "spec": row.get("spec", ""),
        "standard": row.get("standard", ""),
        "material": row.get("material", ""),
        "remark1": row.get("remark1", ""),
        "box_no": row.get("box_no") if int(row.get("box_no", 0) or 0) > 0 else None,
    }


def _heat_record_key(order_no: str, heat_no: str, batch_no: str, material_no: str = "") -> str:
    del order_no
    return f"{heat_no}::{batch_no}::{material_no}"


def _normalize_chemical_values(values: dict[str, Any] | None) -> dict[str, Any]:
    if not values or not isinstance(values, dict):
        return {}
    normalized: dict[str, Any] = {}
    aliases = {
        "C": ["C", "碳C(%) （≤0.23）"],
        "Mn": ["Mn", "锰Mn(%) （0.60-1.65）"],
        "P": ["P", "磷P(%) （≤0.035）"],
        "S": ["S", "硫S(%) （≤0.025）"],
        "Si": ["Si", "硅Si(%) （0.10-0.35）"],
        "Cu": ["Cu", "铜Cu(%) （≤0.40）"],
        "Ni": ["Ni", "镍Ni(%) （≤0.40）"],
        "Cr": ["Cr", "铬Cr(%) （≤0.30）"],
        "Mo": ["Mo", "钼Mo(%) （≤0.12）"],
        "V": ["V", "钒V(%) （≤0.08）"],
        "CEQ": ["CEQ", "CEQ （≤0.36）", "碳当量 CEQ"],
    }
    for target, names in aliases.items():
        for name in names:
            if name in values and values[name] not in (None, ""):
                normalized[target] = values[name]
                break
    return normalized


def _normalize_chemical_analysis_rows(rows: Any) -> list[dict[str, Any]]:
    """统一化学分析结构为 [{raw: {...}, self: {...}}]，兼容按元素分行格式。"""
    if isinstance(rows, dict) and ("raw" in rows or "self" in rows):
        return [
            {
                "raw": rows.get("raw") if isinstance(rows.get("raw"), dict) else {},
                "self": rows.get("self") if isinstance(rows.get("self"), dict) else {},
            }
        ]
    if not isinstance(rows, list) or not rows:
        return [{"raw": {}, "self": {}}]

    first = rows[0] if rows else {}
    if isinstance(first, dict) and ("raw" in first or "self" in first) and "element" not in first:
        return [
            {
                "raw": first.get("raw") if isinstance(first.get("raw"), dict) else {},
                "self": first.get("self") if isinstance(first.get("self"), dict) else {},
            }
        ]

    raw: dict[str, Any] = {}
    self_values: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        element = _normalize_str(row.get("element", ""))
        if not element:
            continue
        if row.get("raw") not in (None, ""):
            raw[element] = row["raw"]
        if row.get("self") not in (None, ""):
            self_values[element] = row["self"]
    return [{"raw": raw, "self": self_values}]


def _heat_row_to_record(row: dict) -> dict:
    hardness_parts = [p.strip() for p in _normalize_str(row.get("mech_hardness", "")).split("/") if p.strip()]
    while len(hardness_parts) < 3:
        hardness_parts.append("")
    return {
        "order_no": row.get("order_no", ""),
        "heat_no": row["heat_no"],
        "batch_no": row["batch_no"],
        "material_no": row.get("material_no", "") or "",
        "mechanical": {
            "test_temp": row.get("mech_test_temp", "") or "RT",
            "yield_strength": row.get("mech_yield_strength", "") or "",
            "tensile_strength": row.get("mech_tensile_strength", "") or "",
            "elongation": row.get("mech_elongation", "") or "",
            "reduction_area": row.get("mech_reduction_area", "") or "",
            "hardness": row.get("mech_hardness", "") or "",
            "hardness_1": hardness_parts[0],
            "hardness_2": hardness_parts[1],
            "hardness_3": hardness_parts[2],
            "impact_test": row.get("mech_impact_test", "") or "",
        },
        "chemical": {
            "raw": {
                "C": _rounded_or_blank(row.get("chem_raw_c")),
                "Mn": _rounded_or_blank(row.get("chem_raw_mn")),
                "P": _rounded_or_blank(row.get("chem_raw_p")),
                "S": _rounded_or_blank(row.get("chem_raw_s")),
                "Si": _rounded_or_blank(row.get("chem_raw_si")),
                "Cu": _rounded_or_blank(row.get("chem_raw_cu")),
                "Ni": _rounded_or_blank(row.get("chem_raw_ni")),
                "Cr": _rounded_or_blank(row.get("chem_raw_cr")),
                "Mo": _rounded_or_blank(row.get("chem_raw_mo")),
                "V": _rounded_or_blank(row.get("chem_raw_v")),
                "CEQ": _rounded_or_blank(row.get("chem_raw_ceq")),
            },
            "self": {
                "C": _rounded_or_blank(row.get("chem_self_c")),
                "Mn": _rounded_or_blank(row.get("chem_self_mn")),
                "P": _rounded_or_blank(row.get("chem_self_p")),
                "S": _rounded_or_blank(row.get("chem_self_s")),
                "Si": _rounded_or_blank(row.get("chem_self_si")),
                "Cu": _rounded_or_blank(row.get("chem_self_cu")),
                "Ni": _rounded_or_blank(row.get("chem_self_ni")),
                "Cr": _rounded_or_blank(row.get("chem_self_cr")),
                "Mo": _rounded_or_blank(row.get("chem_self_mo")),
                "V": _rounded_or_blank(row.get("chem_self_v")),
                "CEQ": _rounded_or_blank(row.get("chem_self_ceq")),
            },
        },
        "test_result": row.get("test_result", "") or "",
        "remark": row.get("remark", "") or "",
        "created_by": row.get("created_by", "") or "",
    }


def _ensure_heat_treatment_records_loaded() -> None:
    if heat_treatment_test_records:
        return
    rows = fetch_all("SELECT * FROM heat_treatment_test_records ORDER BY id")
    for row in rows:
        heat_treatment_test_records[_heat_record_key("", row["heat_no"], row["batch_no"], row.get("material_no", "") or "")] = _heat_row_to_record(row)


def _get_heat_treatment_record(heat_no: str, batch_no: str) -> dict:
    heat_no_text = _normalize_str(heat_no)
    batch_no_text = _normalize_str(batch_no)
    if not heat_no_text or not batch_no_text:
        return {}
    row = fetch_one(
        "SELECT * FROM heat_treatment_test_records WHERE heat_no=%s AND batch_no=%s",
        (heat_no_text, batch_no_text),
    )
    if not row:
        return {}
    record = _heat_row_to_record(row)
    heat_treatment_test_records[_heat_record_key("", heat_no_text, batch_no_text, row.get("material_no", "") or "")] = record
    return record


def _qc_mechanical_readonly_from_trial_db(trial_db: dict | None) -> list[dict]:
    mechanical = heat_treatment_api._trial_row_to_mechanical(trial_db)
    return [
        {"field": "测试温度", "value": mechanical.get("test_temp", "")},
        {"field": "屈服强度", "value": mechanical.get("yield_strength", "")},
        {"field": "抗拉强度", "value": mechanical.get("tensile_strength", "")},
        {"field": "伸长率", "value": mechanical.get("elongation", "")},
        {"field": "断面收缩率", "value": mechanical.get("reduction_area", "")},
        {"field": "硬度1", "value": mechanical.get("hardness_1", "")},
        {"field": "硬度2", "value": mechanical.get("hardness_2", "")},
        {"field": "硬度3", "value": mechanical.get("hardness_3", "")},
        {"field": "-20℃冲击试验", "value": mechanical.get("impact_test", "")},
    ]


def _qc_mechanical_rows_to_certificate_values(rows: list[dict] | None) -> dict[str, str]:
    values: dict[str, str] = {}
    labels = {
        "test_temp": ("测试温度", "Test temp"),
        "yield_strength": ("屈服强度", "Yield"),
        "tensile_strength": ("抗拉强度", "Tensile"),
        "elongation": ("伸长率", "Elongation"),
        "reduction_area": ("断面收缩率", "Reduction"),
        "hardness": ("硬度", "Hardness"),
        "hardness_1": ("硬度1", "Hardness 1"),
        "hardness_2": ("硬度2", "Hardness 2"),
        "hardness_3": ("硬度3", "Hardness 3"),
        "impact_test": ("-20℃冲击试验", "冲击试验", "Impact"),
    }
    for row in rows or []:
        field = _normalize_str(row.get("field", ""))
        value = _normalize_str(row.get("value", ""))
        for key, names in labels.items():
            if field in names:
                values[key] = value
                break
    if not values.get("hardness"):
        hardness_parts = [values.get("hardness_1", ""), values.get("hardness_2", ""), values.get("hardness_3", "")]
        values["hardness"] = "/".join([part for part in hardness_parts if part])
    return values


def _qc_chemical_analysis_to_certificate_values(rows: list[dict] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized_rows = _normalize_chemical_analysis_rows(rows)
    chemical = normalized_rows[0] if normalized_rows else {}
    raw = _normalize_chemical_values(chemical.get("raw", {}))
    self_values = _normalize_chemical_values(chemical.get("self", {}))
    return raw, self_values


def _is_dalian_order_no(order_no: str) -> bool:
    return _normalize_str(order_no).startswith("4")


def _filter_qc_rows_by_region(rows: list[dict], region: str) -> list[dict]:
    region_text = _normalize_str(region).lower()
    if region_text == "dalian":
        return [r for r in rows if _is_dalian_order_no(r.get("order_no", ""))]
    if region_text == "france":
        return [r for r in rows if not _is_dalian_order_no(r.get("order_no", ""))]
    return rows


def _map_material_delivery(material: str) -> tuple[str, str]:
    material_upper = _normalize_str(material).upper()
    if "316" in material_upper:
        return "ASTM A182/A 182M F316L", "固溶退火+水淬 Solution annealing+ quenching in water"
    if "304" in material_upper:
        return "ASTM A182/A 182M F304", "固溶退火+水淬 Solution annealing+ quenching in water"
    if "A105" in material_upper:
        return "ASTM A105", "调质 Quenching +Tempering"
    return "", ""


def _map_dalian_material_delivery(material: str) -> tuple[str, str]:
    return _map_material_delivery(material)


def _qc_base_material_type(material: str) -> str:
    material_upper = _normalize_str(material).upper()
    if "316" in material_upper:
        return "316"
    if "304" in material_upper:
        return "304"
    if "A105" in material_upper:
        return "A105"
    return "A105"


def _lookup_material_ut_mt_pt(material_no: str, drawing_no: str) -> dict[str, str]:
    _ensure_materials_table()
    row = fetch_one(
        """
        SELECT ut, mt, pt FROM materials
        WHERE material_no=%s AND COALESCE(drawing_no, '')=%s
        LIMIT 1
        """,
        (_normalize_str(material_no), _normalize_str(drawing_no)),
    )
    if not row:
        return {"ut": "", "mt": "", "pt": ""}
    return {
        "ut": _normalize_str(row.get("ut", "")),
        "mt": _normalize_str(row.get("mt", "")),
        "pt": _normalize_str(row.get("pt", "")),
    }


def _has_ut_mt_pt_values(material_no: str, drawing_no: str) -> bool:
    values = _lookup_material_ut_mt_pt(material_no, drawing_no)
    return any(_normalize_str(values.get(key, "")) for key in ("ut", "mt", "pt"))


def _form_bool(value: str) -> bool:
    return _normalize_str(value).lower() in ("1", "true", "yes", "on")


def _qc_eligible_utmtpt_template(
    use_utmtpt: bool,
    material: str,
    material_no: str,
    drawing_no: str,
) -> bool:
    """是否选用 A105_UTMTPT 模板。未开启 UTMTPT 时 A105 一律走普通版。"""
    if not use_utmtpt:
        return False
    if _qc_base_material_type(material) != "A105":
        return False
    # 开启 UTMTPT 后的具体业务条件待产品确认，例如：
    # return _has_ut_mt_pt_values(material_no, drawing_no)
    return False


def _resolve_qc_certificate_type(
    material: str,
    material_no: str,
    drawing_no: str,
    *,
    use_utmtpt: bool = False,
) -> str:
    base_type = _qc_base_material_type(material)
    if base_type == "A105" and _qc_eligible_utmtpt_template(use_utmtpt, material, material_no, drawing_no):
        return "A105_UTMTPT"
    return base_type


def _resolve_qc_template(region: str, fmt: str, cert_type: str) -> Path:
    region_dir = "10-DL-qc" if _normalize_str(region).lower() == "dalian" else "11-FR-qc"
    cert_dir = cert_type if cert_type in ("A105", "A105_UTMTPT", "304", "316") else "A105"
    fmt_norm = _normalize_str(fmt).lower()
    if fmt_norm == "pdf":
        primary = TEMPLATES_DIR / region_dir / "pdf" / cert_dir / _QC_PDF_TEMPLATE_FILENAME
        fallback = primary.with_name("quality_certificate.acroform.pdf")
        preview = (
            _STORAGE_DIR
            / "_acroform_build"
            / f"{region_dir.replace('-', '_')}_{cert_dir}.pdf"
        )
        for candidate in (primary, fallback, preview):
            if candidate.is_file():
                if candidate == primary:
                    try:
                        from app.services.pdf_form_renderer import inspect_pdf_form_template

                        report = inspect_pdf_form_template(primary)
                        if report.get("passed_gate"):
                            return primary
                        if fallback.is_file():
                            return fallback
                        if preview.is_file():
                            return preview
                    except Exception:
                        pass
                return candidate
        return primary
    base = TEMPLATES_DIR / region_dir / "word" / cert_dir
    candidates = [
        base / _QC_TEMPLATE_FILENAME,
        base / "quality_certificate.doc",
        TEMPLATES_DIR / "质保书模板.docx",
        TEMPLATES_DIR / "dalian_quality_certificate.docx",
        TEMPLATES_DIR / "大连质保书模板.docx",
        _STORAGE_DIR / "templates" / "dalian_quality_certificate.docx",
    ]
    for path in candidates:
        if path.exists():
            return path
    return base / _QC_TEMPLATE_FILENAME


def _qc_pdf_render_mode() -> str:
    return (get_settings().qc_pdf_render_mode or "word").strip().lower()


def _qc_skips_pdf_direct_fill() -> bool:
    """word / libreoffice 模式：跳过 AcroForm 直填，走 Word 模板渲染后转换。"""
    return _qc_pdf_render_mode() in ("word", "libreoffice")


def _log_qc_pdf_template_gaps(
    region_label: str,
    record_id: int,
    template: Path,
    validation: dict,
) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_name = f"qc_pdf_template_gap_{datetime.now():%Y%m%d}.log"
    payload = {
        "ts": ts,
        "region": region_label,
        "record_id": record_id,
        "template": str(template),
        "leftover_placeholders": validation.get("leftover_placeholders", []),
        "missing_in_output": validation.get("missing_in_output", []),
    }
    _append_log_line(log_name, json.dumps(payload, ensure_ascii=False) + "\n")
    return log_name


def _attempt_qc_pdf_direct_render(
    region: str,
    cert_type: str,
    mapping: dict[str, str],
    download_name: str,
    *,
    record_id: int,
    region_label: str,
) -> tuple[Path, str, str] | None:
    """PDF 直填；成功返回 (path, filename, media_type)，不可用时返回 None。"""
    mode = _qc_pdf_render_mode()
    if _qc_skips_pdf_direct_fill() or mode not in ("direct", "auto"):
        return None

    pdf_template = _resolve_qc_template(region, "pdf", cert_type)
    if not pdf_template.is_file():
        return None

    pdf_name = re.sub(r"\.docx?$", ".pdf", download_name, flags=re.IGNORECASE)
    out_file = _make_temp_output_path(".pdf")
    try:
        validation = fill_pdf_form(pdf_template, out_file, mapping)
        if not validation.get("passed"):
            log_name = _log_qc_pdf_template_gaps(region_label, record_id, pdf_template, validation)
            missing = validation.get("missing_in_output") or []
            leftover = validation.get("leftover_placeholders") or []
            try:
                out_file.unlink(missing_ok=True)
            except OSError:
                pass
            if mode == "auto":
                return None
            detail_parts: list[str] = []
            if missing:
                detail_parts.append(f"未写入字段: {missing[:5]}")
            if leftover:
                detail_parts.append(f"模板仍含 {{{{}}}} 占位符: {leftover[:5]}")
            if not detail_parts:
                detail_parts.append("AcroForm 填表校验未通过")
            raise HTTPException(
                status_code=500,
                detail=(
                    f"PDF AcroForm 填表失败: {'; '.join(detail_parts)}"
                    f"{'...' if len(missing) + len(leftover) > 5 else ''}（详见日志 {log_name}）"
                ),
            )
        if validation.get("missing_in_output"):
            _log_qc_pdf_template_gaps(region_label, record_id, pdf_template, validation)
        return out_file, pdf_name, _MEDIA_PDF
    except HTTPException:
        raise
    except Exception:
        try:
            out_file.unlink(missing_ok=True)
        except OSError:
            pass
        if mode == "direct":
            raise
        return None


def _parse_qc_date(date_text: str) -> datetime:
    text = _normalize_str(date_text)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    parts = [int(p) for p in re.split(r"[-/]", text) if p.strip().isdigit()]
    if len(parts) >= 3:
        return datetime(parts[0], parts[1], parts[2])
    return datetime.now()


def _dalian_certificate_no(date_text: str, seq: int) -> str:
    dt = _parse_qc_date(date_text)
    return f"ZYXMZ{dt.strftime('%y%m%d')}-{seq}"


def _resolve_dalian_cert_allocation(
    row: dict,
    date_text: str,
    *,
    force_regenerate: bool = False,
) -> dict[str, Any]:
    item_no = row.get("item_no")
    return allocate_dalian_certificate(
        row["order_no"],
        row["material_no"],
        item_no,
        date_text,
        force_regenerate=force_regenerate,
    )


def _is_dalian_qc_record(base_info: dict | None) -> bool:
    if not isinstance(base_info, dict):
        return False
    cert_type = _normalize_str(base_info.get("certificate_type", "")).lower()
    return cert_type in ("dalian", "10-dl-qc", "大连")


def _dalian_specifications(row: dict) -> str:
    spec_text = f"{row.get('spec', '')} {row.get('standard', '')}".strip()
    if spec_text:
        return spec_text
    return _normalize_str(row.get("spec_model", ""))


def _dalian_mech_group_from_trial(trial_db: dict | None, heat_no: str) -> dict[str, str]:
    if not trial_db:
        return {}
    mechanical = heat_treatment_api._trial_row_to_mechanical(trial_db)
    return {
        "heat_no": _normalize_str(heat_no),
        "part_no": _normalize_str(trial_db.get("material_no", "")),
        "spec": _normalize_str(trial_db.get("spec_model", "")),
        "yield_strength": _normalize_str(mechanical.get("yield_strength", "")),
        "tensile_strength": _normalize_str(mechanical.get("tensile_strength", "")),
        "elongation": _normalize_str(mechanical.get("elongation", "")),
        "reduction_area": _normalize_str(mechanical.get("reduction_area", "")),
        "hardness": _normalize_str(mechanical.get("hardness", "")),
        "hardness_1": _normalize_str(mechanical.get("hardness_1", "")),
        "hardness_2": _normalize_str(mechanical.get("hardness_2", "")),
        "hardness_3": _normalize_str(mechanical.get("hardness_3", "")),
        "impact_test": _normalize_str(mechanical.get("impact_test", "")),
    }


def _resolve_dalian_certificate_template() -> Path:
    candidates = [
        TEMPLATES_DIR / "dalian_quality_certificate.docx",
        TEMPLATES_DIR / "大连质保书模板.docx",
        _STORAGE_DIR / "templates" / "dalian_quality_certificate.docx",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


DALIAN_MARK_TEMPLATE_DIR = TEMPLATES_DIR / "10-DL-mark"
DALIAN_MARK_TEMPLATE_FILENAMES = (
    "dalian_siemens_shipping_mark.xlsx",
    "大连西门子唛头模板.xlsx",
    "mark.xlsx",
)


def _resolve_dalian_shipping_mark_template() -> Path:
    mark_dir = DALIAN_MARK_TEMPLATE_DIR
    if mark_dir.is_dir():
        for name in DALIAN_MARK_TEMPLATE_FILENAMES:
            path = mark_dir / name
            if path.is_file():
                return path
        for path in sorted(mark_dir.glob("*.xlsx")):
            if path.is_file():
                return path
    legacy = [
        TEMPLATES_DIR / "dalian_siemens_shipping_mark.xlsx",
        TEMPLATES_DIR / "大连西门子唛头模板.xlsx",
        _STORAGE_DIR / "templates" / "dalian_siemens_shipping_mark.xlsx",
    ]
    for path in legacy:
        if path.is_file():
            return path
    return mark_dir / DALIAN_MARK_TEMPLATE_FILENAMES[0]


def _sanitize_file_name_part(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", _normalize_str(value))


def _dalian_mech_groups_to_certificate_values(mechanical_tests: Any) -> tuple[dict[str, str], dict[str, str]]:
    if isinstance(mechanical_tests, dict):
        min_group = mechanical_tests.get("min", {}) or {}
        max_group = mechanical_tests.get("max", {}) or {}
        return min_group, max_group
    return {}, {}


def _build_dalian_mech_mapping(min_group: dict[str, str], max_group: dict[str, str], cert_fn) -> dict[str, str]:
    mapping: dict[str, str] = {}
    field_map = {
        "heat_no": "mech_heat_no",
        "part_no": "mech_part_no",
        "spec": "mech_spec",
        "yield_strength": "mech_yield",
        "tensile_strength": "mech_tensile",
        "elongation": "mech_elongation",
        "reduction_area": "mech_reduction",
        "hardness": "mech_hardness",
        "impact_test": "mech_impact",
    }
    for idx, group in enumerate((min_group, max_group), start=1):
        for src, dst in field_map.items():
            mapping[f"{dst}_{idx}"] = cert_fn(group.get(src, ""))
    return mapping


def _qc_resolve_order_detail_row(order_no: str, material_no: str, heat_no: str = "", heat_treatment_batch_no: str = "", order_detail_id: int | None = None) -> dict:
    _ensure_order_details_loaded()
    if order_detail_id is not None:
        row = next((r for r in order_details if int(r.get("id", 0)) == int(order_detail_id)), None)
        if not row:
            raise HTTPException(status_code=404, detail="未找到对应订单明细")
        if row["order_no"] != order_no or row["material_no"] != material_no:
            raise HTTPException(status_code=400, detail="订单明细与订单号/物料号不一致")
        return row
    rows = [
        r
        for r in order_details
        if r["order_no"] == order_no and r["material_no"] == material_no
    ]
    if not rows:
        raise HTTPException(status_code=404, detail="未找到对应物料")
    hn = _normalize_str(heat_no)
    bn = _normalize_str(heat_treatment_batch_no)
    if hn or bn:
        narrowed = [
            r
            for r in rows
            if (not hn or _normalize_str(r.get("heat_no", "")) == hn)
            and (not bn or _normalize_str(r.get("heat_treatment_batch_no", "")) == bn)
        ]
        if narrowed:
            rows = narrowed
    return rows[0]


ORDER_DETAIL_WRITABLE_FIELDS = frozenset(
    {
        "customer",
        "factory_order_no",
        "seq",
        "item_no",
        "name",
        "drawing_no",
        "material_no",
        "spec_model",
        "spec",
        "standard",
        "material",
        "quantity",
        "unit_weight",
        "total_weight",
        "agreement_price",
        "product_unit_price",
        "remark1",
        "remark2",
        "heat_no",
        "heat_treatment_batch_no",
        "status",
        "upload_type",
        "material_mode",
    }
)

_ORDER_DETAIL_SELECT_COLUMNS = """
        id, order_no, customer, factory_order_no, seq, item_no, name, drawing_no, material_no, spec_model, spec, standard,
        material, quantity, unit_weight, total_weight, agreement_price, product_unit_price, remark1, remark2,
        heat_no, heat_treatment_batch_no, order_status, doc_status, upload_type, material_mode,
        started_at, finished_at, packed_at, shipped_at, created_at, updated_at
"""


def _serialize_order_detail_patch(patch: dict) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in patch.items():
        if key not in ORDER_DETAIL_WRITABLE_FIELDS:
            continue
        if key == "seq":
            normalized["seq"] = _safe_int(value)
        elif key == "customer":
            normalized["customer"] = _normalize_str(value)
        elif key == "item_no":
            if value in (None, ""):
                normalized["item_no"] = None
            else:
                normalized["item_no"] = int(float(value))
        elif key == "quantity":
            normalized["quantity"] = _safe_int(value)
        elif key in ("unit_weight", "total_weight", "agreement_price", "product_unit_price"):
            normalized[key] = _safe_float(value)
        elif key == "status":
            normalized["order_status"] = _normalize_str(value)
        else:
            normalized[key] = _normalize_str(value) if isinstance(value, str) else value
    return normalized


DOC_STATUS_PENDING = "待补资料"


def _ensure_order_details_doc_status_column() -> None:
    row = fetch_one(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'order_details' AND column_name = 'doc_status'
        LIMIT 1
        """
    )
    if not row:
        execute(
            """
            ALTER TABLE order_details
            ADD COLUMN doc_status VARCHAR(32) NOT NULL DEFAULT '' COMMENT '资料状态：空/待补资料'
            AFTER order_status
            """
        )


def _ensure_order_details_price_columns() -> None:
    for column_name, stmt in [
        (
            "agreement_price",
            "ALTER TABLE order_details ADD COLUMN agreement_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '协议价' AFTER total_weight",
        ),
        (
            "product_unit_price",
            "ALTER TABLE order_details ADD COLUMN product_unit_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '产品单价' AFTER agreement_price",
        ),
    ]:
        row = fetch_one(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'order_details' AND column_name = %s
            LIMIT 1
            """,
            (column_name,),
        )
        if not row:
            execute(stmt)


def _drawing_archive_exists(drawing_no: str) -> bool:
    d_no = _normalize_str(drawing_no)
    if not d_no:
        return False
    try:
        from app.api.v1.drawing_archives import _ensure_tables as _ensure_drawing_tables

        _ensure_drawing_tables()
    except Exception:
        pass
    row = fetch_one("SELECT id FROM drawing_archives WHERE drawing_no=%s LIMIT 1", (d_no,))
    return bool(row)


def _material_archive_exists(material_no: str, drawing_no: str = "") -> bool:
    m_no = _normalize_str(material_no)
    if not m_no:
        return False
    try:
        _ensure_materials_table()
    except Exception:
        pass
    row = fetch_one(
        """
        SELECT id FROM materials
        WHERE material_no=%s AND COALESCE(drawing_no, '')=%s
        LIMIT 1
        """,
        (m_no, _normalize_str(drawing_no)),
    )
    return bool(row)


def _compute_doc_status(drawing_no: str, material_no: str = "") -> str:
    """图纸缺失或物料建档缺失 → 待补资料。"""
    d_no = _normalize_str(drawing_no)
    m_no = _normalize_str(material_no)
    if not d_no or not _drawing_archive_exists(d_no):
        return DOC_STATUS_PENDING
    if not m_no or not _material_archive_exists(m_no, d_no):
        return DOC_STATUS_PENDING
    return ""


def _refresh_doc_status_for_order_rows(*, drawing_no: str | None = None, material_no: str | None = None) -> int:
    """按条件重算订单明细资料状态，返回状态发生变化的行数。"""
    _ensure_order_details_doc_status_column()
    _ensure_order_details_loaded()
    d_filter = _normalize_str(drawing_no) if drawing_no is not None else None
    m_filter = _normalize_str(material_no) if material_no is not None else None
    changed = 0
    for r in order_details:
        d_no = _normalize_str(r.get("drawing_no", ""))
        m_no = _normalize_str(r.get("material_no", ""))
        if d_filter is not None and d_no != d_filter:
            continue
        if m_filter is not None and m_no != m_filter:
            continue
        next_status = _compute_doc_status(d_no, m_no)
        prev_status = _normalize_str(r.get("doc_status", ""))
        if prev_status == next_status:
            continue
        execute(
            "UPDATE order_details SET doc_status=%s, updated_at=NOW() WHERE id=%s",
            (next_status, int(r["id"])),
        )
        r["doc_status"] = next_status
        changed += 1
    return changed


def clear_pending_doc_status_for_drawing_no(drawing_no: str) -> int:
    """图纸建档上传后：重算匹配图纸号订单行的资料状态。"""
    d_no = _normalize_str(drawing_no)
    if not d_no:
        return 0
    return _refresh_doc_status_for_order_rows(drawing_no=d_no)


def clear_pending_doc_status_for_material(material_no: str, drawing_no: str = "") -> int:
    """物料建档完成后：重算匹配物料号(+图纸号)订单行的资料状态。"""
    m_no = _normalize_str(material_no)
    if not m_no:
        return 0
    d_no = _normalize_str(drawing_no)
    return _refresh_doc_status_for_order_rows(material_no=m_no, drawing_no=d_no if d_no else None)


def mark_pending_doc_status_for_drawing_no(drawing_no: str) -> int:
    """图纸删除且同号档案已不存在时：重新标记待补资料。"""
    d_no = _normalize_str(drawing_no)
    if not d_no:
        return 0
    return _refresh_doc_status_for_order_rows(drawing_no=d_no)


def _order_detail_cache_from_db(r: dict) -> dict:
    return {
        "id": r["id"],
        "order_no": r["order_no"],
        "customer": r["customer"],
        "factory_order_no": r.get("factory_order_no", "") or "",
        "seq": r["seq"],
        "item_no": int(r["item_no"]) if r.get("item_no") is not None else None,
        "name": r.get("name", "") or "",
        "drawing_no": r.get("drawing_no", "") or "",
        "material_no": r["material_no"],
        "spec_model": r.get("spec_model", "") or "",
        "spec": r.get("spec", "") or "",
        "standard": r.get("standard", "") or "",
        "material": r.get("material", "") or "",
        "quantity": int(r.get("quantity", 0) or 0),
        "unit_weight": float(r.get("unit_weight", 0) or 0),
        "total_weight": float(r.get("total_weight", 0) or 0),
        "agreement_price": float(r.get("agreement_price", 0) or 0),
        "product_unit_price": float(r.get("product_unit_price", 0) or 0),
        "remark1": r.get("remark1", "") or "",
        "remark2": r.get("remark2", "") or "",
        "heat_no": r.get("heat_no", "") or "",
        "heat_treatment_batch_no": r.get("heat_treatment_batch_no", "") or "",
        "status": r.get("order_status", "") or "",
        "doc_status": r.get("doc_status", "") or "",
        "upload_type": r.get("upload_type", "") or "",
        "material_mode": r.get("material_mode", "") or "",
        "started_at": str(r.get("started_at") or ""),
        "finished_at": str(r.get("finished_at") or ""),
        "packed_at": str(r.get("packed_at") or ""),
        "shipped_at": str(r.get("shipped_at") or ""),
        "created_at": str(r.get("created_at") or ""),
        "updated_at": str(r.get("updated_at") or ""),
    }


def _order_detail_business_key(row: dict) -> tuple[str, int | None]:
    item_no = row.get("item_no")
    return (
        _normalize_str(row.get("material_no", "")),
        int(item_no) if item_no not in (None, "") else None,
    )


def _order_detail_production_key(row: dict) -> tuple[int, str, int | None]:
    item_no = row.get("item_no")
    return (
        int(row["id"]),
        _normalize_str(row.get("material_no", "")),
        int(item_no) if item_no not in (None, "") else None,
    )


def _production_target_keys(items: list[dict]) -> set[tuple[int, str, int | None]]:
    keys: set[tuple[int, str, int | None]] = set()
    for item in items:
        detail_id = item.get("id")
        material_no = _normalize_str(item.get("material_no", ""))
        if detail_id in (None, "") or not material_no:
            continue
        item_no = item.get("item_no")
        keys.add(
            (
                int(detail_id),
                material_no,
                int(item_no) if item_no not in (None, "") else None,
            )
        )
    return keys


def _production_confirm_key(row: dict, order_no: str) -> tuple[int, str, str, int | None]:
    item_no = row.get("item_no")
    return (
        int(row["id"]),
        _normalize_str(order_no),
        _normalize_str(row.get("material_no", "")),
        int(item_no) if item_no not in (None, "") else None,
    )


def _ensure_order_details_loaded() -> None:
    _ensure_order_details_doc_status_column()
    _ensure_order_details_price_columns()
    if order_details and ("doc_status" not in order_details[0] or "agreement_price" not in order_details[0]):
        order_details.clear()
    if order_details:
        return
    rows = fetch_all(
        f"""
        SELECT {_ORDER_DETAIL_SELECT_COLUMNS}
        FROM order_details
        ORDER BY id
        """
    )
    order_details[:] = [_order_detail_cache_from_db(r) for r in rows]


_MATERIALS_SELECT_COLUMNS = """
    id, material_no, part_no, drawing_no, spec_model, material, unit_weight, remark,
    ut, mt, pt, product_unit_price, france_agreement_price, dalian_agreement_price,
    created_by, created_at, updated_by, updated_at
"""


def _ensure_materials_table() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS materials (
            id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '系统内部唯一流水号',
            material_no VARCHAR(100) NOT NULL COMMENT '物料号',
            part_no VARCHAR(100) NULL COMMENT '料号',
            drawing_no VARCHAR(255) NULL COMMENT '图纸号',
            spec_model VARCHAR(255) NULL COMMENT '规格型号',
            material VARCHAR(100) NULL COMMENT '材质',
            unit_weight DECIMAL(10,3) NOT NULL DEFAULT 0.000 COMMENT '单重',
            remark TEXT NULL COMMENT '备注',
            ut VARCHAR(16) NULL COMMENT 'UT检测',
            mt VARCHAR(16) NULL COMMENT 'MT检测',
            pt VARCHAR(16) NULL COMMENT 'PT检测',
            product_unit_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '产品单价',
            france_agreement_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '法国协议价',
            dalian_agreement_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '大连协议价',
            created_by VARCHAR(50) NULL COMMENT '建档人',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '建档时间',
            updated_by VARCHAR(50) NULL COMMENT '更新者',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
            INDEX idx_material_no (material_no),
            INDEX idx_part_no (part_no),
            INDEX idx_drawing_no (drawing_no)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='物料基础数据表'
        """
    )
    for column_name, stmt in [
        ("ut", "ALTER TABLE materials ADD COLUMN ut VARCHAR(16) NULL COMMENT 'UT检测' AFTER remark"),
        ("mt", "ALTER TABLE materials ADD COLUMN mt VARCHAR(16) NULL COMMENT 'MT检测' AFTER ut"),
        ("pt", "ALTER TABLE materials ADD COLUMN pt VARCHAR(16) NULL COMMENT 'PT检测' AFTER mt"),
        ("product_unit_price", "ALTER TABLE materials ADD COLUMN product_unit_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '产品单价' AFTER pt"),
        ("france_agreement_price", "ALTER TABLE materials ADD COLUMN france_agreement_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '法国协议价' AFTER product_unit_price"),
        ("dalian_agreement_price", "ALTER TABLE materials ADD COLUMN dalian_agreement_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '大连协议价' AFTER france_agreement_price"),
    ]:
        row = fetch_one(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'materials' AND column_name = %s
            LIMIT 1
            """,
            (column_name,),
        )
        if not row:
            execute(stmt)


def _material_row_from_db(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "material_no": row.get("material_no", "") or "",
        "part_no": row.get("part_no", "") or "",
        "drawing_no": row.get("drawing_no", "") or "",
        "spec_model": row.get("spec_model", "") or "",
        "material": row.get("material", "") or "",
        "unit_weight": float(row.get("unit_weight", 0) or 0),
        "remark": row.get("remark", "") or "",
        "ut": row.get("ut", "") or "",
        "mt": row.get("mt", "") or "",
        "pt": row.get("pt", "") or "",
        "product_unit_price": float(row.get("product_unit_price", 0) or 0),
        "france_agreement_price": float(row.get("france_agreement_price", 0) or 0),
        "dalian_agreement_price": float(row.get("dalian_agreement_price", 0) or 0),
        "created_by": row.get("created_by", "") or "",
        "created_at": str(row.get("created_at") or ""),
        "updated_by": row.get("updated_by", "") or "",
        "updated_at": str(row.get("updated_at") or ""),
    }


def _material_drawing_key(material_no: str, drawing_no: str) -> tuple[str, str]:
    return (_normalize_str(material_no), _normalize_str(drawing_no))


def _find_material_duplicate(material_no: str, drawing_no: str, exclude_id: int | None = None) -> dict | None:
    key_no, key_drawing = _material_drawing_key(material_no, drawing_no)
    if not key_no:
        return None
    row = fetch_one(
        """
        SELECT id, material_no, drawing_no FROM materials
        WHERE material_no = %s AND COALESCE(drawing_no, '') = %s
        LIMIT 1
        """,
        (key_no, key_drawing),
    )
    if not row:
        return None
    row_id = int(row["id"])
    if exclude_id is not None and row_id == int(exclude_id):
        return None
    return row


def _load_materials(material_no: str = "", drawing_no: str = "") -> list[dict]:
    _ensure_materials_table()
    material_no_text = _normalize_str(material_no)
    drawing_no_text = _normalize_str(drawing_no)
    conditions: list[str] = []
    params: list[Any] = []
    if material_no_text:
        conditions.append("material_no LIKE %s")
        params.append(f"%{material_no_text}%")
    if drawing_no_text:
        conditions.append("drawing_no LIKE %s")
        params.append(f"%{drawing_no_text}%")
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = fetch_all(
        f"""
        SELECT {_MATERIALS_SELECT_COLUMNS}
        FROM materials
        {where_clause}
        ORDER BY id
        """,
        tuple(params),
    )
    materials[:] = [_material_row_from_db(r) for r in rows]
    return materials


_PACKING_INIT_EXCLUDED_STATUSES = ("开始", "分割", "加工", "再加工")
_PACKING_KEY_BLOCKED_FIELDS = frozenset({"quantity", "material_no", "item_no"})
_PACKING_KEY_BLOCK_DETAIL = (
    "该明细已关联装箱数据，请先在装箱页删除该订单的相关装箱信息后再修改数量/物料号/条目。"
)


def _item_no_key(value: Any) -> int:
    return normalize_item_no_key(value)


def _is_packing_eligible_status(status: str) -> bool:
    return _normalize_str(status) not in _PACKING_INIT_EXCLUDED_STATUSES


def _packing_order_join_sql() -> str:
    return """
        LEFT JOIN order_details o
          ON o.order_no = p.order_no
         AND o.material_no = p.material_no
         AND COALESCE(o.item_no, 0) = COALESCE(p.entry_no, 0)
    """


def _count_order_rows_for_packing_key(
    order_no: str,
    material_no: str,
    item_no: Any,
    *,
    exclude_id: int | None = None,
) -> int:
    sql = """
        SELECT COUNT(*) AS cnt FROM order_details
        WHERE order_no=%s AND material_no=%s AND COALESCE(item_no, 0)=%s
    """
    params: list[Any] = [order_no, material_no, _item_no_key(item_no)]
    if exclude_id is not None:
        sql += " AND id<>%s"
        params.append(int(exclude_id))
    row = fetch_one(sql, tuple(params))
    return int((row or {}).get("cnt") or 0)


def _clear_packing_if_key_orphaned(
    order_no: str,
    material_no: str,
    item_no: Any,
    *,
    reload: bool = True,
) -> int:
    if _count_order_rows_for_packing_key(order_no, material_no, item_no) > 0:
        return 0
    rows = fetch_all(
        """
        SELECT id FROM packing_details
        WHERE order_no=%s AND material_no=%s AND COALESCE(entry_no, 0)=%s
        """,
        (order_no, material_no, _item_no_key(item_no)),
    )
    if not rows:
        return 0
    execute_many("DELETE FROM packing_details WHERE id=%s", [(int(r["id"]),) for r in rows])
    if reload:
        _reload_packing_details_cache()
    return len(rows)


def _sync_packing_attrs_from_order(after_row: dict) -> None:
    order_no = after_row.get("order_no", "")
    material_no = after_row.get("material_no", "")
    item_key = _item_no_key(after_row.get("item_no"))
    unit_weight = round(float(after_row.get("unit_weight", 0) or 0), 2)
    execute(
        """
        UPDATE packing_details
        SET spec=%s, standard=%s, material=%s, remark1=%s, unit_weight=%s,
            total_weight=ROUND(quantity * %s, 2), updated_at=NOW()
        WHERE order_no=%s AND material_no=%s AND COALESCE(entry_no, 0)=%s
        """,
        (
            after_row.get("spec", "") or "",
            after_row.get("standard", "") or "",
            after_row.get("material", "") or "",
            after_row.get("remark1", "") or "",
            unit_weight,
            unit_weight,
            order_no,
            material_no,
            item_key,
        ),
    )
    _reload_packing_details_cache()


def _packing_draft_values_from_order(order_no: str, r: dict) -> tuple[Any, ...]:
    qty = int(r.get("quantity", 0) or 0)
    unit_weight = round(float(r.get("unit_weight", 0) or 0), 2)
    total_weight = round(float(r.get("total_weight", 0) or 0), 2)
    if not total_weight:
        total_weight = round(qty * unit_weight, 2)
    return (
        order_no,
        r.get("material_no", "") or "",
        r.get("spec", "") or "",
        r.get("standard", "") or "",
        r.get("material", "") or "",
        qty,
        unit_weight,
        total_weight,
        0.0,
        r.get("remark1", "") or "",
        0,
        int(r["item_no"]) if r.get("item_no") is not None else None,
        0,
        0,
        0,
        "",
        None,
        None,
    )


_PACKING_INSERT_SQL = """
    INSERT INTO packing_details (
        order_no, material_no, spec, standard, material, quantity, unit_weight, total_weight,
        gross_weight, remark1, box_no, entry_no, box_length, box_width, box_height,
        packing_remark, delivery_date, packed_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _ensure_packing_row_for_order_detail(order_row: dict) -> bool:
    order_no = order_row.get("order_no", "")
    if not order_no:
        return False
    if not fetch_one("SELECT id FROM packing_details WHERE order_no=%s LIMIT 1", (order_no,)):
        return False
    status = order_row.get("status") or order_row.get("order_status") or ""
    if not _is_packing_eligible_status(str(status)):
        return False
    material_no = order_row.get("material_no", "") or ""
    item_key = _item_no_key(order_row.get("item_no"))
    if fetch_one(
        """
        SELECT id FROM packing_details
        WHERE order_no=%s AND material_no=%s AND COALESCE(entry_no, 0)=%s
        LIMIT 1
        """,
        (order_no, material_no, item_key),
    ):
        return False
    execute(_PACKING_INSERT_SQL, _packing_draft_values_from_order(order_no, order_row))
    _reload_packing_details_cache()
    return True


def _ensure_packing_details_loaded() -> None:
    if packing_details:
        return
    rows = fetch_all(
        f"""
        SELECT p.id, p.order_no, p.material_no, p.spec, p.standard, p.material, p.quantity, p.unit_weight, p.total_weight,
               p.gross_weight, p.remark1, p.box_no, COALESCE(p.entry_no, o.item_no) AS entry_no, p.box_length, p.box_width, p.box_height,
               p.packing_remark, p.delivery_date, p.packed_at, p.created_at, p.updated_at
        FROM packing_details p
        {_packing_order_join_sql()}
        ORDER BY p.id
        """
    )
    packing_details[:] = [_packing_row_from_db(r) for r in rows]


def _reload_packing_details_cache() -> None:
    packing_details.clear()
    _ensure_packing_details_loaded()


def _fetch_packing_rows_from_db(order_no: str) -> list[dict]:
    rows = fetch_all(
        f"""
        SELECT p.id, p.order_no, p.material_no, p.spec, p.standard, p.material, p.quantity, p.unit_weight, p.total_weight,
               p.gross_weight, p.remark1, p.box_no, COALESCE(p.entry_no, o.item_no) AS entry_no, p.box_length, p.box_width, p.box_height,
               p.packing_remark, p.delivery_date, p.packed_at, p.created_at, p.updated_at
        FROM packing_details p
        {_packing_order_join_sql()}
        WHERE p.order_no=%s
        ORDER BY p.box_no, COALESCE(p.entry_no, o.item_no, 0), p.id
        """,
        (order_no,),
    )
    return [_packing_row_from_db(r) for r in rows]


def _initialize_packing_rows(order_no: str) -> None:
    existing = fetch_all(
        "SELECT material_no, entry_no FROM packing_details WHERE order_no=%s",
        (order_no,),
    )
    existing_keys = {
        (str(r.get("material_no") or ""), _item_no_key(r.get("entry_no")))
        for r in existing
    }
    source_rows = fetch_all(
        """
        SELECT id, material_no, spec, standard, material, quantity, unit_weight, total_weight, remark1, item_no
        FROM order_details
        WHERE order_no=%s AND order_status NOT IN ('开始', '分割', '加工', '再加工')
        ORDER BY id
        """,
        (order_no,),
    )
    to_insert: list[tuple[Any, ...]] = []
    for r in source_rows:
        key = (str(r.get("material_no") or ""), _item_no_key(r.get("item_no")))
        if key in existing_keys:
            continue
        existing_keys.add(key)
        to_insert.append(_packing_draft_values_from_order(order_no, r))
    if not to_insert:
        return
    execute_many(_PACKING_INSERT_SQL, to_insert)
    _reload_packing_details_cache()


def _ensure_quality_records_loaded() -> None:
    if quality_records:
        return
    rows = fetch_all("SELECT * FROM qc_records ORDER BY id")
    for r in rows:
        quality_records[int(r["id"])] = {
            "id": int(r["id"]),
            "order_no": r["order_no"],
            "material_no": r["material_no"],
            "base_info": json.loads(r["base_info"]) if r.get("base_info") else {},
            "delivery_content": json.loads(r["delivery_content"]) if r.get("delivery_content") else [],
            "mechanical_tests": json.loads(r["mechanical_tests"]) if r.get("mechanical_tests") else [],
            "chemical_analysis": _normalize_chemical_analysis_rows(
                json.loads(r["chemical_analysis"]) if r.get("chemical_analysis") else []
            ),
            "certificate_path": r.get("certificate_path", "") or "",
            "created_at": str(r.get("created_at") or ""),
        }


def _extract_spec_standard(spec_model: str) -> tuple[str, str]:
    parts = [p for p in re.split(r"\s+", _normalize_str(spec_model)) if p]
    spec = ""
    standard = ""
    for p in parts:
        up = p.upper()
        if not spec and re.match(r"^(DN\d+|\d+NB|\d+A)$", up):
            spec = up
        if not standard and re.match(r"^(PN\d+|CL\d+|\d+LB|\d+K)$", up):
            standard = up
    return spec, standard


def _status_guard(status: str) -> None:
    if status not in ORDER_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法订单状态: {status}")


def _upload_type_guard(upload_type: str) -> None:
    if upload_type not in ORDER_UPLOAD_TYPES:
        raise HTTPException(status_code=400, detail=f"非法上传方式: {upload_type}")


def _material_mode_guard(mode: str) -> None:
    if mode not in MATERIAL_MODES:
        raise HTTPException(status_code=400, detail=f"非法生产模式: {mode}")


def _parse_excel_rows(file_path: Path) -> list[dict]:
    if load_workbook is None:
        raise HTTPException(status_code=500, detail="缺少openpyxl依赖，无法读取Excel。")
    wb = load_workbook(file_path, data_only=True)
    if "明细" not in wb.sheetnames:
        raise HTTPException(status_code=400, detail="读取失败：找不到【明细】sheet。")
    ws = wb["明细"]

    # 第3行为字段名，第4行开始为数据
    header_row = 3
    data_start = 4
    headers = {
        "A": _normalize_str(ws[f"A{header_row}"].value),
        "B": _normalize_str(ws[f"B{header_row}"].value),
        "C": _normalize_str(ws[f"C{header_row}"].value),
        "D": _normalize_str(ws[f"D{header_row}"].value),
        "E": _normalize_str(ws[f"E{header_row}"].value),
        "F": _normalize_str(ws[f"F{header_row}"].value),
        "G": _normalize_str(ws[f"G{header_row}"].value),
        "H": _normalize_str(ws[f"H{header_row}"].value),
        "I": _normalize_str(ws[f"I{header_row}"].value),
        "J": _normalize_str(ws[f"J{header_row}"].value),
        "K": _normalize_str(ws[f"K{header_row}"].value),
    }
    if not headers["A"] and not headers["B"]:
        raise HTTPException(status_code=400, detail="读取失败：第3行字段名为空。")

    # 容错：D列标题可为“xxx物料号”，只要包含物料号即可
    if "物料号" not in headers["D"]:
        raise HTTPException(status_code=400, detail=f"读取失败：D列字段名不包含“物料号”，实际为：{headers['D']}")

    parsed: list[dict] = []
    row_no = data_start
    while True:
        seq = ws[f"A{row_no}"].value
        name = ws[f"B{row_no}"].value
        material_no = ws[f"D{row_no}"].value
        if seq is None and name is None and material_no is None:
            break

        item = {
            "seq": _safe_int(seq),
            "name": _normalize_str(name),
            "drawing_no": _normalize_str(ws[f"C{row_no}"].value),
            "material_no": _normalize_str(material_no),
            "spec_model": _normalize_str(ws[f"E{row_no}"].value),
            "material": _normalize_str(ws[f"F{row_no}"].value),
            "quantity": _safe_int(ws[f"G{row_no}"].value),
            "unit_weight": _safe_float(ws[f"H{row_no}"].value),
            "total_weight": _safe_float(ws[f"I{row_no}"].value),
            "remark1": _normalize_str(ws[f"J{row_no}"].value),
            "remark2": _normalize_str(ws[f"K{row_no}"].value),
        }
        if not item["material_no"]:
            raise HTTPException(status_code=400, detail=f"读取失败：第{row_no}行物料号为空。")
        parsed.append(item)
        row_no += 1
    if not parsed:
        raise HTTPException(status_code=400, detail="读取失败：明细数据为空。")
    return parsed


class ManualOrderRow(BaseModel):
    seq: int
    item_no: int | None = None
    name: str
    drawing_no: str = ""
    material_no: str
    spec_model: str = ""
    material: str = ""
    quantity: int = 0
    unit_weight: float = 0
    total_weight: float = 0
    agreement_price: float = 0
    product_unit_price: float = 0
    remark1: str = ""
    remark2: str = ""


class ManualOrderSubmitRequest(BaseModel):
    order_no: str
    factory_order_no: str = ""
    customer: str
    rows: list[ManualOrderRow]


class QueryOrdersRequest(BaseModel):
    order_no: str = ""
    customer: str = ""
    material_no: str = ""
    status: str | list[str] = ""
    limit: int = 500


class SplitOrderRequest(BaseModel):
    order_no: str
    material_no: str | None = None
    selected_material_nos: list[str] = Field(default_factory=list)
    assign_mode: str


class DeleteOrdersRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)


class OrderDetailUpdateRequest(BaseModel):
    id: int
    patch: dict[str, Any] = Field(default_factory=dict)
    confirm_summary: str = ""


class OrderDetailAppendRequest(BaseModel):
    based_on_id: int
    row: dict[str, Any] = Field(default_factory=dict)
    confirm_summary: str = ""


class ProductionSubmitRequest(BaseModel):
    mode: str = ""
    order_no: str = ""
    material_nos: list[str] = Field(default_factory=list)
    items: list[dict] = Field(default_factory=list)
    heat_no: str = ""
    heat_treatment_batch_no: str = ""


class ProductionLoadRequest(BaseModel):
    mode: str = ""
    order_no: str = ""
    material_no: str = ""
    customer: str = ""


class ProductionBatchApplyRequest(BaseModel):
    order_no: str = ""
    items: list[dict] = Field(default_factory=list)
    heat_no: str = ""
    heat_treatment_batch_no: str = ""


class ProductionConfirmRequest(BaseModel):
    order_no: str = ""
    rows: list[dict]


class ProductionInboundRequest(BaseModel):
    order_no: str = ""
    row_ids: list[int] = Field(default_factory=list)
    items: list[dict] = Field(default_factory=list)


class MaterialsSaveRequest(BaseModel):
    rows: list[dict] = Field(default_factory=list)
    updated_by: str = ""


class MaterialsLookupRequest(BaseModel):
    rows: list[dict] = Field(default_factory=list)


class QcStartRequest(BaseModel):
    order_detail_id: int | None = None
    order_no: str
    material_no: str
    heat_no: str = ""
    heat_treatment_batch_no: str = ""


class QcSaveRequest(BaseModel):
    order_detail_id: int | None = None
    order_no: str
    material_no: str
    heat_no: str = ""
    heat_treatment_batch_no: str = ""
    base_info: dict
    delivery_content: list[dict]
    mechanical_tests: dict[str, Any] | list[dict]
    chemical_analysis: list[dict]


class QcDalianAllocateRequest(BaseModel):
    order_no: str
    material_no: str
    item_no: int | None = None
    date: str
    order_detail_id: int | None = None
    force_regenerate: bool = False


class QcDalianConfirmDateRow(BaseModel):
    order_detail_id: int
    order_no: str
    material_no: str
    item_no: int | None = None


class QcDalianConfirmDateRequest(BaseModel):
    date: str
    rows: list[QcDalianConfirmDateRow]
    force_regenerate: bool = False


class QcDalianUpdateCertificateNoRequest(BaseModel):
    order_no: str
    material_no: str
    item_no: int | None = None
    order_detail_id: int | None = None
    date: str
    certificate_no: str


class QcSnapshotBatchGenerateRequest(BaseModel):
    snapshot_ids: list[int] = Field(default_factory=list)
    region: str = ""
    output_format: str = "word"
    use_utmtpt: bool = False


class PackingStartRequest(BaseModel):
    order_no: str


class PackingRow(BaseModel):
    material_no: str
    quantity: int


class PackingDraftRow(BaseModel):
    id: int | None = None
    source_order_detail_id: int | None = None
    material_no: str
    item_no: int | None = None
    quantity: int = 0
    unit_weight: float = 0
    total_weight: float = 0
    spec: str = ""
    standard: str = ""
    material: str = ""
    remark1: str = ""
    box_no: int | None = None


class PackingSaveRequest(BaseModel):
    order_no: str
    rows: list[PackingDraftRow]
    deleted_ids: list[int] = Field(default_factory=list)


class PackingSubmitItem(BaseModel):
    row_id: int
    box_no: int


class PackingSubmitRequest(BaseModel):
    order_no: str
    items: list[PackingSubmitItem]
    box_length: int
    box_width: int
    box_height: int
    packing_remark: str = ""
    gross_weight: float = 0


class PackingFinishRequest(BaseModel):
    order_no: str


class GeneratePackingListRequest(BaseModel):
    order_no: str
    factory_order_no: str = ""


class GenerateShippingMarkRequest(BaseModel):
    order_no: str
    box_no: int | None = None
    box_nos: list[int] = Field(default_factory=list)
    title: str = "ZHANGQIU MINGYUAN MACHINERY CO.,LTD"
    detail_format: str = (
        "PO NO.：{{po_no}} Item NO.：{{item_no}} R3P Material NO：{{material_no}}\n"
        "Specifications：{{standard}} {{spec}} Qty/unit：{{qty}} PCS Net weight：{{net_weight}} KG"
    )
    font_size: int = 12
    length_cm: float = 10
    width_cm: float = 10


class UpdateFactoryOrderNoRequest(BaseModel):
    order_no: str
    factory_order_no: str = ""


class UpdateDeliveryDateRow(BaseModel):
    box_no: int
    delivery_date: str


class UpdateDeliveryDateRequest(BaseModel):
    order_no: str
    rows: list[UpdateDeliveryDateRow]


class AdvanceShippingStatusRequest(BaseModel):
    order_no: str
    target_status: str


class DalianShippingDetailRow(BaseModel):
    id: int | None = None
    delivery_date: str = ""
    packing_no: str = ""
    drawing_no: str = ""
    ap1_material_no: str = ""
    r3p_material_no: str = ""
    spec_model: str = ""
    material: str = ""
    quantity: int = 0
    unit_weight: float = 0
    total_weight: float = 0
    order_no: str = ""
    factory_order_no: str = ""
    item_no: str = ""
    unit_price: float | None = None
    remark: str = ""
    buyer: str = ""
    heat_no: str = ""
    heat_treatment_batch_no: str = ""
    shipping_mark_remark: str = ""


class DalianShippingSubmitRequest(BaseModel):
    delivery_date: str
    rows: list[DalianShippingDetailRow]
    created_by: str = ""


class DalianShippingGenerateMarkRequest(BaseModel):
    delivery_date: str


class DalianShippingSaveRowRequest(BaseModel):
    delivery_date: str
    row: DalianShippingDetailRow
    created_by: str = ""


def _validate_delivery_date_yyyymmdd(value: str) -> str:
    text = _normalize_str(value)
    if not re.fullmatch(r"\d{8}", text):
        raise HTTPException(status_code=400, detail="发货日期格式应为YYYYMMDD")
    return text


def _format_dalian_mark_date(delivery_date: str) -> str:
    y = delivery_date[:4]
    m = int(delivery_date[4:6])
    d = int(delivery_date[6:8])
    return f"{y}.{m}.{d}"


def _normalize_dalian_row_delivery_date(value: str, fallback: str) -> str:
    text = _normalize_str(value)
    if not text:
        return _validate_delivery_date_yyyymmdd(fallback)
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return _validate_delivery_date_yyyymmdd(digits[:8])
    dotted = re.match(r"^(\d{4})[.\-/年](\d{1,2})[.\-/月](\d{1,2})", text)
    if dotted:
        y, m, d = dotted.groups()
        return _validate_delivery_date_yyyymmdd(f"{y}{int(m):02d}{int(d):02d}")
    return _validate_delivery_date_yyyymmdd(fallback)


def _clean_dalian_order_no(order_no: str) -> str:
    text = _normalize_str(order_no)
    for sep in ("（", "("):
        if sep in text:
            return text.split(sep, 1)[0].strip()
    return text


def _calc_dalian_total_weight(quantity: int, unit_weight: float) -> float:
    return round(float(quantity or 0) * float(unit_weight or 0), 3)


def _is_dalian_a105_material(material: str) -> bool:
    """材质为 A105（含 ASTM A105 等含 A105 的写法）时视为 A105。"""
    material_upper = _normalize_str(material).upper()
    return material_upper == "A105" or "A105" in material_upper


def _infer_dalian_shipping_mark_remark(material: str, heat_treatment_batch_no: str) -> str:
    """A105 且热处理批号非空 → 绿色，否则 → 白色。"""
    has_heat_batch = bool(_normalize_str(heat_treatment_batch_no))
    if _is_dalian_a105_material(material) and has_heat_batch:
        return "绿色"
    return "白色"


def _normalize_dalian_row(row: DalianShippingDetailRow, delivery_date: str) -> dict[str, Any]:
    quantity = _safe_int(row.quantity, 0)
    unit_weight = round(float(row.unit_weight or 0), 3)
    total_weight = _calc_dalian_total_weight(quantity, unit_weight)
    if row.total_weight:
        total_weight = round(float(row.total_weight), 3)
    shipping_mark_remark = _infer_dalian_shipping_mark_remark(
        row.material, row.heat_treatment_batch_no
    )
    resolved_delivery_date = _normalize_dalian_row_delivery_date(row.delivery_date, delivery_date)
    return {
        "delivery_date": resolved_delivery_date,
        "packing_no": _normalize_str(row.packing_no),
        "drawing_no": _normalize_str(row.drawing_no),
        "ap1_material_no": _normalize_str(row.ap1_material_no),
        "r3p_material_no": _normalize_str(row.r3p_material_no),
        "spec_model": _normalize_str(row.spec_model),
        "material": _normalize_str(row.material),
        "quantity": quantity,
        "unit_weight": unit_weight,
        "total_weight": total_weight,
        "order_no": _normalize_str(row.order_no),
        "factory_order_no": _normalize_str(row.factory_order_no),
        "item_no": _normalize_str(row.item_no),
        "unit_price": None if row.unit_price is None else round(float(row.unit_price), 2),
        "remark": _normalize_str(row.remark),
        "buyer": _normalize_str(row.buyer),
        "heat_no": _normalize_str(row.heat_no),
        "heat_treatment_batch_no": _normalize_str(row.heat_treatment_batch_no),
        "shipping_mark_remark": shipping_mark_remark,
    }


_DALIAN_ROW_COMPARE_FIELDS = (
    "delivery_date",
    "packing_no",
    "drawing_no",
    "ap1_material_no",
    "r3p_material_no",
    "spec_model",
    "material",
    "quantity",
    "unit_weight",
    "total_weight",
    "order_no",
    "factory_order_no",
    "item_no",
    "unit_price",
    "remark",
    "buyer",
    "heat_no",
    "heat_treatment_batch_no",
    "shipping_mark_remark",
)


def _dalian_rows_equal(existing: dict[str, Any], normalized: dict[str, Any]) -> bool:
    for field in _DALIAN_ROW_COMPARE_FIELDS:
        left = existing.get(field)
        right = normalized.get(field)
        if field in ("quantity",):
            if _safe_int(left, 0) != _safe_int(right, 0):
                return False
            continue
        if field in ("unit_weight", "total_weight"):
            if round(float(left or 0), 3) != round(float(right or 0), 3):
                return False
            continue
        if field == "unit_price":
            left_val = None if left is None else round(float(left), 2)
            right_val = None if right is None else round(float(right), 2)
            if left_val != right_val:
                return False
            continue
        if _normalize_str(left) != _normalize_str(right):
            return False
    return True


def _dalian_insert_params(normalized: dict[str, Any], created_by: str = "") -> tuple[Any, ...]:
    return (
        normalized["delivery_date"],
        normalized["packing_no"] or None,
        normalized["drawing_no"] or None,
        normalized["ap1_material_no"] or None,
        normalized["r3p_material_no"] or None,
        normalized["spec_model"] or None,
        normalized["material"] or None,
        normalized["quantity"],
        normalized["unit_weight"],
        normalized["total_weight"],
        normalized["order_no"],
        normalized["factory_order_no"] or None,
        normalized["item_no"],
        normalized["unit_price"],
        normalized["remark"] or None,
        normalized["buyer"] or None,
        normalized["heat_no"] or None,
        normalized["heat_treatment_batch_no"] or None,
        normalized["shipping_mark_remark"] or None,
        _normalize_str(created_by) or None,
    )


_DALIAN_INSERT_SQL = """
    INSERT INTO dalian_shipping_details (
        delivery_date, packing_no, drawing_no, ap1_material_no, r3p_material_no,
        spec_model, material, quantity, unit_weight, total_weight, order_no, factory_order_no, item_no,
        unit_price, remark, buyer, heat_no, heat_treatment_batch_no, shipping_mark_remark, created_by
    ) VALUES (
        %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s
    )
"""


_DALIAN_UPDATE_SQL = """
    UPDATE dalian_shipping_details
    SET delivery_date=%s, packing_no=%s, drawing_no=%s, ap1_material_no=%s, r3p_material_no=%s,
        spec_model=%s, material=%s, quantity=%s, unit_weight=%s, total_weight=%s,
        order_no=%s, factory_order_no=%s, item_no=%s, unit_price=%s, remark=%s, buyer=%s,
        heat_no=%s, heat_treatment_batch_no=%s, shipping_mark_remark=%s
    WHERE id=%s
"""


def _get_dalian_max_id() -> int:
    row = fetch_one("SELECT COALESCE(MAX(id), 0) AS max_id FROM dalian_shipping_details")
    return int(row["max_id"]) if row else 0


def _fetch_dalian_row_by_id(row_id: int) -> dict[str, Any] | None:
    row = fetch_one("SELECT * FROM dalian_shipping_details WHERE id=%s", (row_id,))
    return _serialize_dalian_db_row(row) if row else None


def _insert_dalian_row(normalized: dict[str, Any], created_by: str = "") -> dict[str, Any]:
    new_id = execute(_DALIAN_INSERT_SQL, _dalian_insert_params(normalized, created_by))
    saved = _fetch_dalian_row_by_id(int(new_id))
    if not saved:
        raise HTTPException(status_code=500, detail="保存后未能读取发货明细")
    return saved


def _update_dalian_row(row_id: int, normalized: dict[str, Any]) -> dict[str, Any]:
    execute(
        _DALIAN_UPDATE_SQL,
        (
            normalized["delivery_date"],
            normalized["packing_no"] or None,
            normalized["drawing_no"] or None,
            normalized["ap1_material_no"] or None,
            normalized["r3p_material_no"] or None,
            normalized["spec_model"] or None,
            normalized["material"] or None,
            normalized["quantity"],
            normalized["unit_weight"],
            normalized["total_weight"],
            normalized["order_no"],
            normalized["factory_order_no"] or None,
            normalized["item_no"],
            normalized["unit_price"],
            normalized["remark"] or None,
            normalized["buyer"] or None,
            normalized["heat_no"] or None,
            normalized["heat_treatment_batch_no"] or None,
            normalized["shipping_mark_remark"] or None,
            row_id,
        ),
    )
    saved = _fetch_dalian_row_by_id(row_id)
    if not saved:
        raise HTTPException(status_code=500, detail="更新后未能读取发货明细")
    return saved


def _serialize_dalian_db_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "delivery_date": _normalize_str(row.get("delivery_date")),
        "packing_no": _normalize_str(row.get("packing_no")),
        "drawing_no": _normalize_str(row.get("drawing_no")),
        "ap1_material_no": _normalize_str(row.get("ap1_material_no")),
        "r3p_material_no": _normalize_str(row.get("r3p_material_no")),
        "spec_model": _normalize_str(row.get("spec_model")),
        "material": _normalize_str(row.get("material")),
        "quantity": _safe_int(row.get("quantity"), 0),
        "unit_weight": round(float(row.get("unit_weight") or 0), 3),
        "total_weight": round(float(row.get("total_weight") or 0), 3),
        "order_no": _normalize_str(row.get("order_no")),
        "factory_order_no": _normalize_str(row.get("factory_order_no")),
        "item_no": _normalize_str(row.get("item_no")),
        "unit_price": None if row.get("unit_price") is None else round(float(row.get("unit_price")), 2),
        "remark": _normalize_str(row.get("remark")),
        "buyer": _normalize_str(row.get("buyer")),
        "heat_no": _normalize_str(row.get("heat_no")),
        "heat_treatment_batch_no": _normalize_str(row.get("heat_treatment_batch_no")),
        "shipping_mark_remark": _normalize_str(row.get("shipping_mark_remark")),
        "created_by": _normalize_str(row.get("created_by")),
        "created_at": row.get("created_at"),
    }


def _fetch_dalian_rows_by_date(delivery_date: str) -> list[dict[str, Any]]:
    rows = fetch_all(
        """
        SELECT *
        FROM dalian_shipping_details
        WHERE delivery_date = %s
        ORDER BY id ASC
        """,
        (delivery_date,),
    )
    return [_serialize_dalian_db_row(r) for r in rows]


def _copy_dalian_mark_cell_style(ws, src_row: int, dst_row: int) -> None:
    for col in range(1, 11):
        src = ws.cell(src_row, col)
        dst = ws.cell(dst_row, col)
        if src.has_style:
            dst.font = copy(src.font)
            dst.border = copy(src.border)
            dst.fill = copy(src.fill)
            dst.number_format = copy(src.number_format)
            dst.protection = copy(src.protection)
            dst.alignment = copy(src.alignment)
    ref_height = ws.row_dimensions[src_row].height
    if ref_height:
        ws.row_dimensions[dst_row].height = ref_height


def _dalian_mark_scalar(value: str) -> Any:
    text = _normalize_str(value)
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def _set_worksheet_selection_a1(ws) -> None:
    """保存前将活动单元格设为 A1，便于打开文件后光标在表头左上角。"""
    try:
        from openpyxl.worksheet.views import Selection
    except ImportError:
        return
    ws.sheet_view.selection = [Selection(activeCell="A1", sqref="A1")]
    if ws.views.sheetView:
        ws.views.sheetView[0].topLeftCell = "A1"


def _apply_dalian_mark_print_by_packing(ws, data_start_row: int, rows: list[dict[str, Any]]) -> None:
    """打印时重复 1~3 行表头，并按 A 列包装序号（合并组）分页。"""
    from openpyxl.worksheet.pagebreak import Break, RowBreak

    ws.print_title_rows = "1:3"
    if not rows:
        return

    row_break = RowBreak()
    prev_packing: str | None = None
    for idx, row in enumerate(rows):
        packing_no = _normalize_str(row.get("packing_no"))
        if idx > 0 and packing_no != prev_packing:
            row_break.append(Break(id=data_start_row + idx))
        prev_packing = packing_no

    if row_break.brk:
        ws.row_breaks = row_break


def _build_dalian_mark_workbook(rows: list[dict[str, Any]], delivery_date: str):
    if load_workbook is None:
        raise HTTPException(status_code=500, detail="缺少openpyxl依赖，无法生成大连唛头。")

    template_path = _resolve_dalian_shipping_mark_template()
    if not template_path.exists():
        raise HTTPException(
            status_code=500,
            detail=(
                "未找到大连唛头模板，请将 xlsx 模板放入 "
                "templates/10-DL-mark/ 目录"
            ),
        )

    wb = load_workbook(template_path)
    ws = wb.active
    ws.title = "Sheet1"
    formatted_date = _format_dalian_mark_date(delivery_date)
    ws["I2"].value = formatted_date

    data_start_row = 4
    style_ref_row = 4
    if ws.max_row > data_start_row:
        ws.delete_rows(data_start_row + 1, ws.max_row - data_start_row)

    row_count = len(rows)
    if row_count > 1:
        ws.insert_rows(data_start_row + 1, row_count - 1)

    packing_groups: list[tuple[int, int]] = []
    group_start: int | None = None
    prev_packing: str | None = None

    for idx, row in enumerate(rows):
        excel_row = data_start_row + idx
        if excel_row != style_ref_row:
            _copy_dalian_mark_cell_style(ws, style_ref_row, excel_row)

        packing_no = _normalize_str(row.get("packing_no"))
        if packing_no != prev_packing:
            if group_start is not None and prev_packing and idx - group_start > 1:
                packing_groups.append((group_start, idx - 1))
            group_start = idx if packing_no else None
            prev_packing = packing_no or None

        quantity = _safe_int(row.get("quantity"), 0)
        unit_weight = round(float(row.get("unit_weight") or 0), 3)
        show_packing = packing_no if group_start == idx else ""

        ws.cell(excel_row, 1).value = _dalian_mark_scalar(show_packing) if show_packing else None
        ws.cell(excel_row, 2).value = _dalian_mark_scalar(_clean_dalian_order_no(_normalize_str(row.get("order_no"))))
        ws.cell(excel_row, 3).value = _dalian_mark_scalar(_normalize_str(row.get("item_no")))
        ws.cell(excel_row, 4).value = _dalian_mark_scalar(_normalize_str(row.get("ap1_material_no")))
        ws.cell(excel_row, 5).value = _normalize_str(row.get("spec_model")) or None
        ws.cell(excel_row, 6).value = quantity
        ws.cell(excel_row, 7).value = unit_weight
        ws.cell(excel_row, 8).value = f"=G{excel_row}*F{excel_row}"
        ws.cell(excel_row, 9).value = _dalian_mark_scalar(_normalize_str(row.get("heat_no")))
        ws.cell(excel_row, 10).value = _normalize_str(row.get("shipping_mark_remark")) or None

    if group_start is not None and prev_packing and len(rows) - group_start > 1:
        packing_groups.append((group_start, len(rows) - 1))

    for start_idx, end_idx in packing_groups:
        start_row = data_start_row + start_idx
        end_row = data_start_row + end_idx
        ws.merge_cells(start_row=start_row, start_column=1, end_row=end_row, end_column=1)

    _apply_dalian_mark_print_by_packing(ws, data_start_row, rows)
    _set_worksheet_selection_a1(ws)
    wb.active = ws
    return wb


@router.get("/packing/suggestions")
def packing_material_suggestions(order_no: str, keyword: str = ""):
    rows = [r for r in order_details if r["order_no"] == order_no]
    if keyword:
        rows = [r for r in rows if keyword.lower() in r["material_no"].lower()]
    options = []
    seen = set()
    for r in rows:
        material_no = r["material_no"]
        if material_no in seen:
            continue
        seen.add(material_no)
        options.append({"value": material_no, "label": f"{material_no} / {r.get('name', '')}"})
    return {"status": "success", "options": options[:50]}


@router.get("/packing/grouped")
def get_packing_grouped(order_no: str):
    rows = [r for r in packing_details if r["order_no"] == order_no]
    grouped: dict[int, dict] = {}
    for r in rows:
        box_no = int(r["box_no"])
        g = grouped.setdefault(
            box_no,
            {"box_no": box_no, "gross_weight": 0.0, "net_weight": 0.0, "items": []},
        )
        g["gross_weight"] += float(r.get("gross_weight", 0))
        g["net_weight"] += float(r.get("total_weight", 0))
        g["items"].append(r)
    return {"status": "success", "grouped": list(grouped.values())}


@router.post("/contracts/import")
async def import_contract_file(
    order_no: str = Form(...),
    customer: str = Form(...),
    file: UploadFile = File(...),
):
    _status_guard("开始")
    _upload_type_guard("文件上传")
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="仅支持xlsx文件。")

    temp_dir = OUTPUT_DIR / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_file = temp_dir / f"upload_{now_local_compact()}.xlsx"
    try:
        content = await file.read()
        temp_file.write_bytes(content)
        rows = _parse_excel_rows(temp_file)
    except HTTPException:
        if temp_file.exists():
            temp_file.unlink()
        raise
    except Exception as exc:
        if temp_file.exists():
            temp_file.unlink()
        raise HTTPException(status_code=400, detail=f"文件读取失败: {exc}") from exc

    created: list[dict] = []
    _ensure_order_details_loaded()
    now = now_iso()
    for row in rows:
        record_id = execute(
            """
            INSERT INTO order_details (
                order_no, customer, factory_order_no, seq, item_no, name, drawing_no, material_no, spec_model, spec, standard, material,
                quantity, unit_weight, total_weight, remark1, remark2, heat_no, heat_treatment_batch_no,
                order_status, upload_type, material_mode, started_at, finished_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                order_no, customer, "", row["seq"], None, row["name"], row["drawing_no"], row["material_no"], row["spec_model"], "",
                "", row["material"], row["quantity"], row["unit_weight"], row["total_weight"], row["remark1"], row["remark2"],
                "", "", "开始", "文件上传", "", now, None
            ),
        )
        record = {
            "id": record_id,
            "order_no": order_no,
            "customer": customer,
            "factory_order_no": "",
            "seq": row["seq"],
            "item_no": None,
            "name": row["name"],
            "drawing_no": row["drawing_no"],
            "material_no": row["material_no"],
            "spec_model": row["spec_model"],
            "spec": "",
            "standard": "",
            "material": row["material"],
            "quantity": row["quantity"],
            "unit_weight": row["unit_weight"],
            "total_weight": row["total_weight"],
            "remark1": row["remark1"],
            "remark2": row["remark2"],
            "heat_no": "",
            "heat_treatment_batch_no": "",
            "status": "开始",
            "upload_type": "文件上传",
            "material_mode": "",
            "started_at": now,
            "finished_at": "",
            "updated_at": now,
        }
        order_details.append(record)
        created.append(record)

    if temp_file.exists():
        temp_file.unlink()
    return {"status": "success", "order_no": order_no, "count": len(created), "rows": created}


@router.post("/orders/manual")
def create_manual_order(req: ManualOrderSubmitRequest):
    _status_guard("开始")
    _upload_type_guard("手动录入")

    errors = []
    seq_rows: dict[int, list[int]] = {}
    for idx, row in enumerate(req.rows):
        row_idx = idx + 1
        seq_rows.setdefault(int(row.seq), []).append(row_idx)
        if not row.material_no:
            errors.append({"row": row_idx, "field": "material_no", "message": "物料号不能为空"})
        if row.quantity < 0:
            errors.append({"row": row_idx, "field": "quantity", "message": "数量不能为负"})
        if row.unit_weight < 0:
            errors.append({"row": row_idx, "field": "unit_weight", "message": "单重不能为负"})
    for seq, row_indexes in seq_rows.items():
        if len(row_indexes) > 1:
            errors.append({"row": row_indexes[0], "field": "seq", "message": f"序号{seq}重复，重复行：第{'、'.join(map(str, row_indexes))}行"})
    if errors:
        raise HTTPException(status_code=400, detail={"message": "订单提交失败", "errors": errors})

    now = now_iso()
    _ensure_order_details_loaded()
    existing_seq = {
        int(r.get("seq", 0))
        for r in order_details
        if r.get("order_no") == req.order_no and str(r.get("seq", "")).strip()
    }
    duplicate_existing = sorted(set(seq_rows) & existing_seq)
    if duplicate_existing:
        raise HTTPException(
            status_code=400,
            detail={"message": f"订单号{req.order_no}下序号已存在：{', '.join(map(str, duplicate_existing))}", "errors": []},
        )
    created = []
    pending_count = 0
    for row in req.rows:
        doc_status = _compute_doc_status(row.drawing_no, row.material_no)
        if doc_status == DOC_STATUS_PENDING:
            pending_count += 1
        agreement_price = round(float(row.agreement_price or 0), 2)
        product_unit_price = round(float(row.product_unit_price or 0), 2)
        record_id = execute(
            """
            INSERT INTO order_details (
                order_no, customer, factory_order_no, seq, item_no, name, drawing_no, material_no, spec_model, spec, standard, material,
                quantity, unit_weight, total_weight, agreement_price, product_unit_price, remark1, remark2, heat_no, heat_treatment_batch_no,
                order_status, doc_status, upload_type, material_mode, started_at, finished_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                req.order_no, req.customer, req.factory_order_no, row.seq, row.item_no, row.name, row.drawing_no, row.material_no, row.spec_model, "",
                "", row.material, row.quantity, round(float(row.unit_weight), 2), round(float(row.total_weight), 2),
                agreement_price, product_unit_price, row.remark1, row.remark2,
                "", "", "开始", doc_status, "手动录入", "", now, None
            ),
        )
        record = {
            "id": record_id,
            "order_no": req.order_no,
            "customer": req.customer,
            "factory_order_no": req.factory_order_no,
            "seq": row.seq,
            "item_no": row.item_no,
            "name": row.name,
            "drawing_no": row.drawing_no,
            "material_no": row.material_no,
            "spec_model": row.spec_model,
            "spec": "",
            "standard": "",
            "material": row.material,
            "quantity": row.quantity,
            "unit_weight": round(float(row.unit_weight), 2),
            "total_weight": round(float(row.total_weight), 2),
            "agreement_price": agreement_price,
            "product_unit_price": product_unit_price,
            "remark1": row.remark1,
            "remark2": row.remark2,
            "heat_no": "",
            "heat_treatment_batch_no": "",
            "status": "开始",
            "doc_status": doc_status,
            "upload_type": "手动录入",
            "material_mode": "",
            "started_at": now,
            "finished_at": "",
            "updated_at": now,
        }
        order_details.append(record)
        created.append(record)
    return {
        "status": "success",
        "order_no": req.order_no,
        "count": len(created),
        "pending_docs_count": pending_count,
        "rows": created,
    }


@router.post("/orders/query")
def query_orders(req: QueryOrdersRequest):
    _ensure_order_details_loaded()
    data = order_details
    if req.order_no:
        data = [r for r in data if r["order_no"] == req.order_no]
    if req.customer:
        data = [r for r in data if req.customer.lower() in r["customer"].lower()]
    if req.material_no:
        data = [r for r in data if req.material_no.lower() in r["material_no"].lower()]
    status_filter: list[str] = []
    if isinstance(req.status, list):
        status_filter = [_normalize_str(s) for s in req.status if _normalize_str(s)]
    elif _normalize_str(req.status):
        status_filter = [_normalize_str(req.status)]
    if status_filter:
        data = [r for r in data if r["status"] in status_filter]

    warning = ""
    if not req.order_no and len(data) > req.limit:
        data = data[: req.limit]
        warning = f"结果过多，仅返回最近{req.limit}条，请追加查询条件。"

    # 仅输入订单号时显示整个订单内容
    grouped: dict[str, list[dict]] = {}
    for r in data:
        grouped.setdefault(r["order_no"], []).append(r)

    return {"status": "success", "warning": warning, "total": len(data), "grouped": grouped}


@router.post("/orders/delete")
def delete_orders(req: DeleteOrdersRequest):
    _ensure_order_details_loaded()
    ids = sorted({int(x) for x in req.ids if x is not None})
    if not ids:
        raise HTTPException(status_code=400, detail="请先选择要删除的物料")
    existing_ids = {int(r["id"]) for r in order_details}
    target_ids = [order_id for order_id in ids if order_id in existing_ids]
    if not target_ids:
        raise HTTPException(status_code=404, detail="未找到可删除的订单明细")
    target_id_set = set(target_ids)
    targets = [r for r in order_details if int(r["id"]) in target_id_set]
    packing_keys = {
        (r["order_no"], r["material_no"], _item_no_key(r.get("item_no")))
        for r in targets
    }
    execute_many("DELETE FROM order_details WHERE id=%s", [(order_id,) for order_id in target_ids])
    order_details[:] = [r for r in order_details if int(r["id"]) not in target_id_set]
    packing_deleted_count = 0
    for order_no, material_no, item_key in packing_keys:
        packing_deleted_count += _clear_packing_if_key_orphaned(
            order_no,
            material_no,
            item_key,
            reload=False,
        )
    if packing_deleted_count:
        _reload_packing_details_cache()
    return {
        "status": "success",
        "deleted_count": len(target_ids),
        "ids": target_ids,
        "packing_deleted_count": packing_deleted_count,
    }


@router.post("/orders/detail/update")
def update_order_detail(req: OrderDetailUpdateRequest):
    _ensure_order_details_loaded()
    patch_sql = _serialize_order_detail_patch(req.patch)
    if not patch_sql:
        raise HTTPException(status_code=400, detail="没有可更新的字段")
    idx = next((i for i, r in enumerate(order_details) if int(r["id"]) == req.id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="未找到订单明细")
    before_row = dict(order_details[idx])
    if _has_packing_association_for_detail(before_row):
        for field in _PACKING_KEY_BLOCKED_FIELDS:
            if field not in patch_sql:
                continue
            before_val = before_row.get(field)
            after_val = patch_sql.get(field)
            if field == "item_no":
                changed = _item_no_key(before_val) != _item_no_key(after_val)
            elif field == "quantity":
                changed = int(before_val or 0) != int(after_val or 0)
            else:
                changed = _normalize_str(before_val) != _normalize_str(after_val)
            if changed:
                raise HTTPException(status_code=400, detail=_PACKING_KEY_BLOCK_DETAIL)
    cols = list(patch_sql.keys())
    sql = "UPDATE order_details SET " + ", ".join(f"{c}=%s" for c in cols) + ", updated_at=NOW() WHERE id=%s"
    execute(sql, tuple(patch_sql[c] for c in cols) + (req.id,))
    # 图纸号/物料号变更（或任意更新后）按最新值重算资料状态
    drawing_after = patch_sql.get("drawing_no") if "drawing_no" in patch_sql else before_row.get("drawing_no", "")
    material_after = patch_sql.get("material_no") if "material_no" in patch_sql else before_row.get("material_no", "")
    doc_status = _compute_doc_status(str(drawing_after or ""), str(material_after or ""))
    execute("UPDATE order_details SET doc_status=%s, updated_at=NOW() WHERE id=%s", (doc_status, req.id))
    fresh = fetch_one(
        f"""
        SELECT {_ORDER_DETAIL_SELECT_COLUMNS}
        FROM order_details WHERE id=%s
        """,
        (req.id,),
    )
    if fresh:
        order_details[idx] = _order_detail_cache_from_db(fresh)
    _sync_packing_attrs_from_order(order_details[idx])
    log_payload = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": "update",
        "id": req.id,
        "before": before_row,
        "patch_applied": patch_sql,
        "confirm_summary": req.confirm_summary,
    }
    _append_log_line(f"order_detail_change_{datetime.now().strftime('%Y-%m-%d')}.log", json.dumps(log_payload, ensure_ascii=False, default=str) + "\n")
    return {"status": "success", "row": order_details[idx]}


@router.post("/orders/detail/append")
def append_order_detail(req: OrderDetailAppendRequest):
    _ensure_order_details_loaded()
    base = next((r for r in order_details if int(r["id"]) == req.based_on_id), None)
    if not base:
        raise HTTPException(status_code=404, detail="未找到参照明细")
    patch_sql = _serialize_order_detail_patch(req.row)
    customer = _normalize_str(req.row.get("customer", base["customer"]))
    order_no = base["order_no"]
    factory_order_no = patch_sql.get("factory_order_no") if "factory_order_no" in patch_sql else base["factory_order_no"]
    same_order = [r for r in order_details if r["order_no"] == order_no]
    max_seq = max((int(r["seq"]) for r in same_order), default=0)
    seq_val = int(patch_sql["seq"]) if "seq" in patch_sql else max_seq + 1
    material_no = patch_sql.get("material_no") if "material_no" in patch_sql else base["material_no"]
    if not material_no:
        raise HTTPException(status_code=400, detail="追加行物料号不能为空")
    item_val = patch_sql.get("item_no") if "item_no" in patch_sql else (base["item_no"] if base.get("item_no") is not None else None)
    name_val = patch_sql.get("name") if "name" in patch_sql else base["name"]
    drawing_val = patch_sql.get("drawing_no") if "drawing_no" in patch_sql else base["drawing_no"]
    spec_model_val = patch_sql.get("spec_model") if "spec_model" in patch_sql else base["spec_model"]
    spec_val = patch_sql.get("spec") if "spec" in patch_sql else base["spec"]
    standard_val = patch_sql.get("standard") if "standard" in patch_sql else base["standard"]
    material_val = patch_sql.get("material") if "material" in patch_sql else base["material"]
    qty_val = int(patch_sql["quantity"]) if "quantity" in patch_sql else int(base["quantity"])
    uw_val = float(patch_sql["unit_weight"]) if "unit_weight" in patch_sql else float(base["unit_weight"])
    tw_val = float(patch_sql["total_weight"]) if "total_weight" in patch_sql else float(base["total_weight"])
    r1_val = patch_sql.get("remark1") if "remark1" in patch_sql else base["remark1"]
    r2_val = patch_sql.get("remark2") if "remark2" in patch_sql else base["remark2"]
    agreement_val = float(patch_sql["agreement_price"]) if "agreement_price" in patch_sql else float(base.get("agreement_price", 0) or 0)
    product_price_val = float(patch_sql["product_unit_price"]) if "product_unit_price" in patch_sql else float(base.get("product_unit_price", 0) or 0)
    heat_val = patch_sql.get("heat_no") if "heat_no" in patch_sql else base["heat_no"]
    batch_val = patch_sql.get("heat_treatment_batch_no") if "heat_treatment_batch_no" in patch_sql else base["heat_treatment_batch_no"]
    status_val = patch_sql.get("order_status") if "order_status" in patch_sql else base["status"]
    upload_val = patch_sql.get("upload_type") if "upload_type" in patch_sql else base["upload_type"]
    mode_val = patch_sql.get("material_mode") if "material_mode" in patch_sql else base["material_mode"]
    doc_status = _compute_doc_status(str(drawing_val or ""), str(material_no or ""))
    now = now_iso()
    record_id = execute(
        """
        INSERT INTO order_details (
            order_no, customer, factory_order_no, seq, item_no, name, drawing_no, material_no, spec_model, spec, standard, material,
            quantity, unit_weight, total_weight, agreement_price, product_unit_price, remark1, remark2, heat_no, heat_treatment_batch_no,
            order_status, doc_status, upload_type, material_mode, started_at, finished_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            order_no,
            customer,
            factory_order_no,
            seq_val,
            item_val,
            name_val,
            drawing_val,
            material_no,
            spec_model_val,
            spec_val,
            standard_val,
            material_val,
            qty_val,
            uw_val,
            tw_val,
            agreement_val,
            product_price_val,
            r1_val,
            r2_val,
            heat_val,
            batch_val,
            status_val,
            doc_status,
            upload_val,
            mode_val,
            now,
            None,
        ),
    )
    fresh = fetch_one(
        f"""
        SELECT {_ORDER_DETAIL_SELECT_COLUMNS}
        FROM order_details WHERE id=%s
        """,
        (record_id,),
    )
    appended = _order_detail_cache_from_db(fresh) if fresh else {}
    order_details.append(appended)
    packing_inserted = _ensure_packing_row_for_order_detail(appended) if appended else False
    log_payload = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": "append",
        "based_on_id": req.based_on_id,
        "new_id": record_id,
        "confirm_summary": req.confirm_summary,
        "packing_inserted": packing_inserted,
        "snapshot": appended,
    }
    _append_log_line(f"order_detail_change_{datetime.now().strftime('%Y-%m-%d')}.log", json.dumps(log_payload, ensure_ascii=False, default=str) + "\n")
    return {"status": "success", "row": appended, "packing_inserted": packing_inserted}


@router.post("/orders/split")
def split_order(req: SplitOrderRequest):
    _ensure_order_details_loaded()
    _material_mode_guard(req.assign_mode)
    target = [r for r in order_details if r["order_no"] == req.order_no]
    if not target:
        raise HTTPException(status_code=404, detail="订单不存在")
    if req.material_no:
        target = [r for r in target if r["material_no"] == req.material_no]
    if req.selected_material_nos:
        target = [r for r in target if r["material_no"] in req.selected_material_nos]
    if not target:
        raise HTTPException(status_code=400, detail="没有可分配的物料")

    now = now_iso()
    for r in target:
        r["material_mode"] = req.assign_mode
        r["status"] = "分割"
        r["updated_at"] = now
    execute_many(
        "UPDATE order_details SET material_mode=%s, order_status=%s, updated_at=NOW() WHERE id=%s",
        [(req.assign_mode, "分割", int(r["id"])) for r in target],
    )
    return {"status": "success", "order_no": req.order_no, "updated_count": len(target), "rows": target}


@router.post("/production/submit")
def submit_production(req: ProductionSubmitRequest):
    _ensure_order_details_loaded()
    if req.mode:
        _material_mode_guard(req.mode)
    order_no = _normalize_str(req.order_no)
    target_keys = _production_target_keys(req.items)
    target = [
        r
        for r in order_details
        if (not order_no or r["order_no"] == order_no)
        and (
            (target_keys and _order_detail_production_key(r) in target_keys)
            or (not target_keys and r["material_no"] in req.material_nos)
        )
        and (not req.mode or r["material_mode"] == req.mode)
    ]
    if not target:
        raise HTTPException(status_code=404, detail="未找到符合条件的物料")

    confirm_rows = []
    for r in target:
        spec, standard = _extract_spec_standard(r["spec_model"])
        confirm_rows.append(
            {
                "id": r["id"],
                "material_no": r["material_no"],
                "item_no": r.get("item_no"),
                "spec_model": r["spec_model"],
                "spec": spec,
                "standard": standard,
                "heat_no": req.heat_no,
                "heat_treatment_batch_no": req.heat_treatment_batch_no,
            }
        )
    return {"status": "success", "order_no": req.order_no, "rows_for_confirm": confirm_rows}


@router.post("/production/load")
def load_production_rows(req: ProductionLoadRequest):
    _ensure_order_details_loaded()
    if req.mode:
        _material_mode_guard(req.mode)
    order_no = _normalize_str(req.order_no)
    material_no = _normalize_str(req.material_no)
    customer = _normalize_str(req.customer)
    if not order_no and not material_no:
        raise HTTPException(status_code=400, detail="请至少输入订单号或物料号")
    rows = [
        r
        for r in order_details
        if (not order_no or r["order_no"] == order_no)
        and (not material_no or r["material_no"] == material_no)
        and (not customer or customer.lower() in r.get("customer", "").lower())
        and (not req.mode or r["material_mode"] == req.mode)
    ]
    if not rows:
        raise HTTPException(status_code=404, detail="未找到符合条件的物料")
    return {"status": "success", "rows": rows}


@router.post("/production/batch-apply")
def batch_apply_production(req: ProductionBatchApplyRequest):
    _ensure_order_details_loaded()
    target_keys = _production_target_keys(req.items)
    if not target_keys:
        raise HTTPException(status_code=400, detail="请选择要批量应用的物料")
    heat_no = _normalize_str(req.heat_no)
    batch_no = _normalize_str(req.heat_treatment_batch_no)
    if not heat_no and not batch_no:
        raise HTTPException(status_code=400, detail="炉号和热处理批号均为空，无需批量应用")
    order_no = _normalize_str(req.order_no)
    now = now_iso()
    updated = []
    for r in order_details:
        if order_no and r["order_no"] != order_no:
            continue
        if _order_detail_production_key(r) not in target_keys:
            continue
        if heat_no:
            r["heat_no"] = heat_no
        if batch_no:
            r["heat_treatment_batch_no"] = batch_no
        r["updated_at"] = now
        updated.append(r)
    if not updated:
        raise HTTPException(status_code=404, detail="未找到可批量应用的数据")
    execute_many(
        """
        UPDATE order_details
        SET heat_no=CASE WHEN %s='' THEN heat_no ELSE %s END,
            heat_treatment_batch_no=CASE WHEN %s='' THEN heat_treatment_batch_no ELSE %s END,
            updated_at=NOW()
        WHERE id=%s
        """,
        [(heat_no, heat_no, batch_no, batch_no, int(r["id"])) for r in updated],
    )
    return {"status": "success", "updated_count": len(updated), "rows": updated}


@router.post("/production/confirm")
def confirm_production(req: ProductionConfirmRequest):
    _ensure_order_details_loaded()
    order_no = _normalize_str(req.order_no)
    now = now_iso()
    updated = []
    confirm_id_map = {
        int(row["id"]): row
        for row in req.rows
        if row.get("id") not in (None, "")
    }
    if not confirm_id_map:
        raise HTTPException(status_code=400, detail="没有可确认的数据")
    for r in order_details:
        row = confirm_id_map.get(int(r["id"]))
        if row is None:
            continue
        if order_no and r["order_no"] != order_no:
            continue
        r["spec"] = _normalize_str(row.get("spec"))
        r["standard"] = _normalize_str(row.get("standard"))
        r["heat_no"] = _normalize_str(row.get("heat_no"))
        r["heat_treatment_batch_no"] = _normalize_str(row.get("heat_treatment_batch_no"))
        r["quantity"] = _safe_int(row.get("quantity"), int(r.get("quantity", 0) or 0))
        r["status"] = "加工"
        r["updated_at"] = now
        updated.append(r)
    if not updated:
        raise HTTPException(status_code=400, detail="没有可确认的数据")
    execute_many(
        """
        UPDATE order_details
        SET spec=%s, standard=%s, heat_no=%s, heat_treatment_batch_no=%s, quantity=%s, order_status=%s, updated_at=NOW()
        WHERE id=%s
        """,
        [
            (
                r["spec"],
                r["standard"],
                r["heat_no"],
                r["heat_treatment_batch_no"],
                int(r.get("quantity", 0) or 0),
                "加工",
                int(r["id"]),
            )
            for r in updated
        ],
    )
    return {"status": "success", "updated_count": len(updated), "rows": updated}


@router.post("/production/inbound")
def inbound_order(req: ProductionInboundRequest):
    _ensure_order_details_loaded()
    order_no = _normalize_str(req.order_no)
    target_keys = _production_target_keys(req.items)
    target = [
        r
        for r in order_details
        if (not order_no or r["order_no"] == order_no)
        and (
            (target_keys and _order_detail_production_key(r) in target_keys)
            or (not target_keys and req.row_ids and int(r["id"]) in req.row_ids)
        )
    ]
    if not target:
        raise HTTPException(status_code=404, detail="未找到可入库的数据")
    invalid = [r for r in target if r.get("status") != "加工"]
    if invalid:
        material_nos = "、".join(_normalize_str(r.get("material_no", "")) for r in invalid)
        raise HTTPException(status_code=400, detail=f"以下物料不是加工状态，无法入库：{material_nos}")
    now = now_iso()
    for r in target:
        r["status"] = "入库"
        r["updated_at"] = now
    execute_many(
        "UPDATE order_details SET order_status=%s, updated_at=NOW() WHERE id=%s",
        [("入库", int(r["id"])) for r in target],
    )
    return {"status": "success", "order_no": req.order_no, "updated_count": len(target)}


@router.get("/materials")
def list_materials(material_no: str = "", drawing_no: str = ""):
    rows = _load_materials(material_no, drawing_no)
    return {"status": "success", "total": len(rows), "rows": rows}


@router.post("/materials/lookup")
def lookup_materials(req: MaterialsLookupRequest):
    _ensure_materials_table()
    result_rows: list[dict] = []
    missing_rows: list[dict] = []
    for idx, raw in enumerate(req.rows):
        source_index = int(raw.get("source_index", idx))
        drawing_no = _normalize_str(raw.get("drawing_no", ""))
        material_no = _normalize_str(raw.get("material_no", ""))
        if not drawing_no or not material_no:
            missing_rows.append({"source_index": source_index, "drawing_no": drawing_no, "material_no": material_no, "reason": "图纸号或物料号为空"})
            continue
        row = fetch_one(
            f"""
            SELECT {_MATERIALS_SELECT_COLUMNS}
            FROM materials
            WHERE material_no=%s AND COALESCE(drawing_no, '')=%s
            ORDER BY id
            LIMIT 1
            """,
            (material_no, drawing_no),
        )
        if not row:
            missing_rows.append({"source_index": source_index, "drawing_no": drawing_no, "material_no": material_no, "reason": "未找到匹配物料"})
            continue
        material_row = _material_row_from_db(row)
        material_row["source_index"] = source_index
        result_rows.append(material_row)
    return {"status": "success", "count": len(result_rows), "rows": result_rows, "missing": missing_rows}


@router.post("/materials/submit")
def submit_materials(req: MaterialsSaveRequest):
    _ensure_materials_table()
    saved_ids: list[int] = []
    inserted_count = 0
    updated_count = 0
    operator = _normalize_str(req.updated_by)
    for raw in req.rows:
        material_no = _normalize_str(raw.get("material_no", ""))
        if not material_no:
            raise HTTPException(status_code=400, detail="物料号不能为空")
        drawing_no = _normalize_str(raw.get("drawing_no", ""))
        unit_weight = _safe_float(raw.get("unit_weight"), 0.0)
        row_id = raw.get("id")
        exclude_id = int(row_id) if row_id else None
        duplicate = _find_material_duplicate(material_no, drawing_no, exclude_id=exclude_id)
        if duplicate:
            raise HTTPException(
                status_code=400,
                detail=f"物料号与图纸号组合已存在（物料号={material_no}，图纸号={drawing_no or '空'}，已有记录 id={duplicate['id']}）",
            )
        payload = (
            material_no,
            _normalize_str(raw.get("part_no", "")),
            drawing_no,
            _normalize_str(raw.get("spec_model", "")),
            _normalize_str(raw.get("material", "")),
            unit_weight,
            _normalize_str(raw.get("remark", "")),
            _normalize_str(raw.get("ut", "")) or None,
            _normalize_str(raw.get("mt", "")) or None,
            _normalize_str(raw.get("pt", "")) or None,
            _safe_float(raw.get("product_unit_price"), 0.0),
            _safe_float(raw.get("france_agreement_price"), 0.0),
            _safe_float(raw.get("dalian_agreement_price"), 0.0),
            operator,
        )
        if row_id:
            existing = fetch_one("SELECT id FROM materials WHERE id=%s", (int(row_id),))
            if existing:
                execute(
                    """
                    UPDATE materials
                    SET material_no=%s, part_no=%s, drawing_no=%s, spec_model=%s, material=%s,
                        unit_weight=%s, remark=%s, ut=%s, mt=%s, pt=%s,
                        product_unit_price=%s, france_agreement_price=%s, dalian_agreement_price=%s,
                        updated_by=%s, updated_at=NOW()
                    WHERE id=%s
                    """,
                    (*payload, int(row_id)),
                )
                saved_ids.append(int(row_id))
                updated_count += 1
                continue
        new_id = execute(
            """
            INSERT INTO materials (
                material_no, part_no, drawing_no, spec_model, material, unit_weight, remark,
                ut, mt, pt, product_unit_price, france_agreement_price, dalian_agreement_price,
                created_by, updated_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (*payload, operator),
        )
        saved_ids.append(new_id)
        inserted_count += 1
    if not saved_ids:
        return {
            "status": "success",
            "saved_count": 0,
            "inserted_count": 0,
            "updated_count": 0,
            "rows": _load_materials(),
        }
    rows = fetch_all(
        f"""
        SELECT {_MATERIALS_SELECT_COLUMNS}
        FROM materials
        WHERE id IN ({",".join(["%s"] * len(saved_ids))})
        ORDER BY id
        """,
        tuple(saved_ids),
    )
    saved_rows = [_material_row_from_db(r) for r in rows]
    _load_materials()
    cleared_pending = 0
    for saved in saved_rows:
        cleared_pending += clear_pending_doc_status_for_material(
            str(saved.get("material_no") or ""),
            str(saved.get("drawing_no") or ""),
        )
    return {
        "status": "success",
        "saved_count": len(saved_rows),
        "inserted_count": inserted_count,
        "updated_count": updated_count,
        "cleared_pending_docs_count": cleared_pending,
        "rows": saved_rows,
    }


@router.delete("/materials/{material_id}")
def delete_material(material_id: int):
    _ensure_materials_table()
    existing = fetch_one(
        "SELECT id, material_no, drawing_no FROM materials WHERE id=%s",
        (material_id,),
    )
    if not existing:
        raise HTTPException(status_code=404, detail="未找到要删除的物料")
    material_no = _normalize_str(existing.get("material_no", ""))
    drawing_no = _normalize_str(existing.get("drawing_no", ""))
    execute("DELETE FROM materials WHERE id=%s", (material_id,))
    _load_materials()
    marked_pending = 0
    if material_no:
        marked_pending = _refresh_doc_status_for_order_rows(material_no=material_no, drawing_no=drawing_no or None)
    return {"status": "success", "deleted_id": material_id, "marked_pending_docs_count": marked_pending}


@router.get("/qc/list")
def qc_list(order_no: str = "", material_no: str = "", region: str = "", status: str = ""):
    _ensure_order_details_loaded()
    status_text = _normalize_str(status)
    if status_text:
        if status_text not in ORDER_STATUSES:
            raise HTTPException(status_code=400, detail=f"非法订单状态: {status_text}")
        target = [r for r in order_details if r["status"] == status_text]
    else:
        target = [r for r in order_details if r["status"] not in ("开始", "分割", "加工")]
    if order_no:
        target = [r for r in target if r["order_no"] == order_no]
    if material_no:
        target = [r for r in target if material_no.lower() in r["material_no"].lower()]
    target = _filter_qc_rows_by_region(target, region)
    enriched = [enrich_order_detail_with_snapshot(r) for r in target]
    return {"status": "success", "total": len(enriched), "rows": enriched}


@router.get("/qc/snapshots")
def qc_snapshot_list(order_no: str = "", material_no: str = "", region: str = "", status: str = ""):
    status_text = _normalize_str(status)
    if not _normalize_str(order_no) and not _normalize_str(material_no) and not status_text:
        raise HTTPException(status_code=400, detail="请至少填写订单号、物料号或状态")
    if status_text and status_text not in ORDER_STATUSES:
        raise HTTPException(status_code=400, detail=f"非法订单状态: {status_text}")
    rows = query_snapshots(region=region, order_no=order_no, material_no=material_no, status=status_text)
    return {"status": "success", "total": len(rows), "rows": rows}


@router.get("/qc/snapshots/{snapshot_id}")
def qc_snapshot_detail(snapshot_id: int):
    snap = get_snapshot_by_id(snapshot_id)
    if not snap:
        raise HTTPException(status_code=404, detail="未找到质保书快照")
    return {"status": "success", "snapshot": snap}


def _persist_generated_certificate(record_id: int, file_name: str) -> None:
    quality_records[record_id]["certificate_path"] = file_name
    execute("UPDATE qc_records SET certificate_path=%s, updated_at=NOW() WHERE id=%s", (file_name, record_id))
    record = dict(quality_records[record_id])
    record["certificate_path"] = file_name
    record["id"] = record_id
    upsert_snapshot_from_record(
        record,
        order_detail_id=record.get("order_detail_id"),
        qc_record_id=record_id,
        certificate_path=file_name,
    )


def _snapshot_to_save_request(snap: dict) -> QcSaveRequest:
    delivery = snap.get("delivery_content") or []
    delivery0 = delivery[0] if delivery else {}
    return QcSaveRequest(
        order_detail_id=int(snap["order_detail_id"]) if snap.get("order_detail_id") else None,
        order_no=str(snap.get("order_no", "")),
        material_no=str(snap.get("material_no", "")),
        heat_no=_normalize_str(delivery0.get("raw_material_no", snap.get("heat_no", ""))),
        heat_treatment_batch_no=_normalize_str(delivery0.get("batch_no", snap.get("heat_treatment_batch_no", ""))),
        base_info=dict(snap.get("base_info") or {}),
        delivery_content=list(delivery),
        mechanical_tests=snap.get("mechanical_tests") or [],
        chemical_analysis=list(snap.get("chemical_analysis") or []),
    )


@router.post("/qc/snapshots/batch_generate")
def qc_snapshot_batch_generate(req: QcSnapshotBatchGenerateRequest):
    """从快照批量 save 并返回 record_id 列表，供前端逐条调用 generate 下载文件。"""
    if not req.snapshot_ids:
        raise HTTPException(status_code=400, detail="请至少选择一个快照")
    results: list[dict[str, Any]] = []
    for snapshot_id in req.snapshot_ids:
        snap = get_snapshot_by_id(int(snapshot_id))
        if not snap:
            results.append({"snapshot_id": int(snapshot_id), "ok": False, "error": "快照不存在"})
            continue
        region_text = _normalize_str(req.region).lower()
        snap_type = _normalize_str(snap.get("certificate_type", "")).lower()
        if region_text == "dalian" and snap_type != "dalian":
            results.append({"snapshot_id": int(snapshot_id), "ok": False, "error": "快照不属于大连质检"})
            continue
        if region_text == "france" and snap_type == "dalian":
            results.append({"snapshot_id": int(snapshot_id), "ok": False, "error": "快照不属于法国质检"})
            continue
        try:
            save_result = save_qc(_snapshot_to_save_request(snap))
            results.append(
                {
                    "snapshot_id": int(snapshot_id),
                    "ok": True,
                    "record_id": save_result["record_id"],
                }
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            results.append({"snapshot_id": int(snapshot_id), "ok": False, "error": detail})
        except Exception as exc:
            results.append({"snapshot_id": int(snapshot_id), "ok": False, "error": str(exc)})
    return {"status": "success", "results": results}


@router.post("/qc/start")
def start_qc(req: QcStartRequest):
    row = _qc_resolve_order_detail_row(req.order_no, req.material_no, req.heat_no, req.heat_treatment_batch_no, req.order_detail_id)
    now = now_iso()
    row["status"] = "质检"
    row["updated_at"] = now
    execute(
        "UPDATE order_details SET order_status=%s, updated_at=NOW() WHERE id=%s",
        ("质检", int(row["id"])),
    )
    return {"status": "success", "rows": [row]}


@router.get("/qc/detail")
def get_qc_detail(
    order_no: str,
    material_no: str,
    heat_no: str = "",
    heat_treatment_batch_no: str = "",
    order_detail_id: int | None = None,
):
    row = _qc_resolve_order_detail_row(order_no, material_no, heat_no, heat_treatment_batch_no, order_detail_id)
    item_text = _normalize_str(row.get("item_no", ""))
    cert_item_suffix = item_text[:-1] if len(item_text) > 1 else item_text or "1"
    same_item_rows = [
        r
        for r in order_details
        if r["order_no"] == order_no
        and r["material_no"] == material_no
        and _normalize_str(r.get("item_no", "")) == item_text
    ]
    same_item_rows.sort(key=lambda r: (_safe_int(r.get("seq"), 0), int(r.get("id", 0))))
    duplicate_suffix = ""
    if len(same_item_rows) > 1:
        row_id = int(row.get("id", 0))
        row_index = next((idx + 1 for idx, candidate in enumerate(same_item_rows) if int(candidate.get("id", 0)) == row_id), 1)
        duplicate_suffix = f"-{row_index}"
    certificate_no = f"ZYXMZ{order_no}-{cert_item_suffix}{duplicate_suffix}"
    default_date = datetime.now().strftime("%Y-%m-%d")
    material_text, delivery_state = _map_material_delivery(row.get("material", ""))
    base_info = {
        "certificate_no": certificate_no,
        "customer": row.get("customer", ""),
        "contract_no": row.get("order_no", ""),
        "date": default_date,
        "works_no": row.get("order_no", ""),
        "article": "锻造法兰 Forged Flange",
        "state_of_delivery": delivery_state or "调质 Quenching +Tempering",
        "material": material_text or "ASTM A105",
        "marking": "工厂品牌、标准、尺寸、压力、材料等级、炉号 Factory brand, Standard, Dimension, Pressure, Material grade, Heat No.",
    }
    delivery = {
        "qty": row.get("quantity", 0),
        "drawing_no": row.get("drawing_no", ""),
        "part_no": row.get("material_no", ""),
        "item_no": row.get("item_no", ""),
        "specifications": f"{row.get('spec', '')} {row.get('standard', '')}".strip(),
        "raw_material_no": row.get("heat_no", ""),
        "batch_no": row.get("heat_treatment_batch_no", ""),
    }
    trial_rows, trial_db = heat_treatment_api._select_trial_db_row_for_material(
        row.get("heat_no", ""),
        row.get("heat_treatment_batch_no", ""),
        row.get("material_no", ""),
    )
    trial_count = len(trial_rows)
    mechanical_readonly = _qc_mechanical_readonly_from_trial_db(trial_db)

    # 模块四：同炉号多条时取最近更新的一条（updated_at 视同最近录入/维护时间）
    chem_row = fetch_one(
        """
        SELECT * FROM heat_treatment_chemical_records
        WHERE heat_no=%s
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (_normalize_str(row.get("heat_no", "")),),
    )
    chem_payload = heat_treatment_api._chemical_row_to_payload(chem_row)
    chemical_raw = _normalize_chemical_values(chem_payload.get("raw", {}))
    chemical_self = _normalize_chemical_values(chem_payload.get("self", {}))

    trial_notice = None
    if trial_count == 0:
        trial_notice = {
            "level": "missing",
            "heat_no": _normalize_str(row.get("heat_no", "")),
            "batch_no": _normalize_str(row.get("heat_treatment_batch_no", "")),
        }

    source_record = _get_heat_treatment_record(row.get("heat_no", ""), row.get("heat_treatment_batch_no", ""))
    return {
        "status": "success",
        "base_info": base_info,
        "delivery_result": delivery,
        "order_row": row,
        "heat_treatment_record": source_record,
        "mechanical_readonly": mechanical_readonly,
        "chemical_readonly": {
            "raw": chemical_raw,
            "self": chemical_self,
        },
        "trial_data_notice": trial_notice,
        "qc_context": {
            "order_no": row["order_no"],
            "order_detail_id": int(row["id"]),
            "material_no": row["material_no"],
            "heat_no": _normalize_str(row.get("heat_no", "")),
            "heat_treatment_batch_no": _normalize_str(row.get("heat_treatment_batch_no", "")),
        },
    }


@router.get("/qc/dalian/detail")
def get_qc_dalian_detail(
    order_no: str,
    material_no: str,
    heat_no: str = "",
    heat_treatment_batch_no: str = "",
    order_detail_id: int | None = None,
    date: str = "",
    force_regenerate: bool = False,
    allocate: bool = False,
):
    row = _qc_resolve_order_detail_row(order_no, material_no, heat_no, heat_treatment_batch_no, order_detail_id)
    if not _is_dalian_order_no(order_no):
        raise HTTPException(status_code=400, detail="该订单不属于大连质检范围")
    default_date = _normalize_str(date) or datetime.now().strftime("%Y-%m-%d")
    cert_allocation: dict[str, Any] | None = None
    certificate_no = ""
    if allocate:
        cert_allocation = _resolve_dalian_cert_allocation(row, default_date, force_regenerate=force_regenerate)
        certificate_no = cert_allocation["certificate_no"]
    material_text, delivery_state = _map_dalian_material_delivery(row.get("material", ""))
    base_info = {
        "certificate_type": "dalian",
        "certificate_no": certificate_no,
        "customer": row.get("customer", ""),
        "contract_no": row.get("order_no", ""),
        "date": default_date,
        "works_no": row.get("order_no", ""),
        "article": "锻造法兰 Forged Flange",
        "state_of_delivery": delivery_state,
        "material": material_text,
        "marking": "工厂品牌、标准、尺寸、压力、材料等级、炉号 Factory brand, Standard, Dimension, Pressure, Material grade, Heat No.",
    }
    delivery = {
        "qty": row.get("quantity", 0),
        "drawing_no": row.get("drawing_no", ""),
        "part_no": row.get("material_no", ""),
        "name": row.get("name", ""),
        "item_no": row.get("item_no", ""),
        "specifications": _dalian_specifications(row),
        "raw_material_no": row.get("heat_no", ""),
        "batch_no": row.get("heat_treatment_batch_no", ""),
    }
    heat_no_text = _normalize_str(row.get("heat_no", ""))
    batch_no_text = _normalize_str(row.get("heat_treatment_batch_no", ""))
    trial_rows, min_row, max_row = heat_treatment_api._select_trial_min_max_rows(heat_no_text, batch_no_text)
    mechanical_readonly = {
        "min": _dalian_mech_group_from_trial(min_row, heat_no_text),
        "max": _dalian_mech_group_from_trial(max_row, heat_no_text),
    }

    chem_row = fetch_one(
        """
        SELECT * FROM heat_treatment_chemical_records
        WHERE heat_no=%s
        ORDER BY updated_at DESC, id DESC
        LIMIT 1
        """,
        (heat_no_text,),
    )
    chem_payload = heat_treatment_api._chemical_row_to_payload(chem_row)
    chemical_raw = _normalize_chemical_values(chem_payload.get("raw", {}))
    chemical_self = _normalize_chemical_values(chem_payload.get("self", {}))

    trial_notice = None
    if not trial_rows:
        trial_notice = {
            "level": "missing",
            "heat_no": heat_no_text,
            "batch_no": batch_no_text,
        }

    source_record = _get_heat_treatment_record(heat_no_text, batch_no_text)
    return {
        "status": "success",
        "base_info": base_info,
        "cert_allocation": cert_allocation,
        "delivery_result": delivery,
        "order_row": row,
        "heat_treatment_record": source_record,
        "mechanical_readonly": mechanical_readonly,
        "chemical_readonly": {
            "raw": chemical_raw,
            "self": chemical_self,
        },
        "trial_data_notice": trial_notice,
        "qc_context": {
            "order_no": row["order_no"],
            "order_detail_id": int(row["id"]),
            "material_no": row["material_no"],
            "heat_no": heat_no_text,
            "heat_treatment_batch_no": batch_no_text,
        },
    }


@router.post("/qc/dalian/allocate")
def allocate_dalian_certificate_no(req: QcDalianAllocateRequest):
    _ensure_order_details_loaded()
    row = _qc_resolve_order_detail_row(
        req.order_no,
        req.material_no,
        "",
        "",
        req.order_detail_id,
    )
    if not _is_dalian_order_no(req.order_no):
        raise HTTPException(status_code=400, detail="该订单不属于大连质检范围")
    item_no = req.item_no if req.item_no is not None else row.get("item_no")
    allocation = allocate_dalian_certificate(
        req.order_no,
        req.material_no,
        item_no,
        req.date,
        force_regenerate=req.force_regenerate,
    )
    return {"status": "success", "cert_allocation": allocation, "certificate_no": allocation["certificate_no"]}


@router.post("/qc/dalian/confirm_date")
def confirm_dalian_factory_date(req: QcDalianConfirmDateRequest):
    _ensure_order_details_loaded()
    if not _normalize_str(req.date):
        raise HTTPException(status_code=400, detail="出厂日期不能为空")
    if not req.rows:
        raise HTTPException(status_code=400, detail="请至少选择一行物料")
    sorted_rows = sorted(req.rows, key=lambda r: int(r.order_detail_id))
    results: list[dict[str, Any]] = []
    for item in sorted_rows:
        row = _qc_resolve_order_detail_row(
            item.order_no,
            item.material_no,
            "",
            "",
            item.order_detail_id,
        )
        if not _is_dalian_order_no(item.order_no):
            raise HTTPException(status_code=400, detail=f"订单 {item.order_no} 不属于大连质检范围")
        item_no = item.item_no if item.item_no is not None else row.get("item_no")
        allocation = allocate_dalian_certificate(
            item.order_no,
            item.material_no,
            item_no,
            req.date,
            force_regenerate=req.force_regenerate,
        )
        results.append(
            {
                "order_detail_id": int(item.order_detail_id),
                "order_no": item.order_no,
                "material_no": item.material_no,
                "item_no": item_no,
                "certificate_no": allocation["certificate_no"],
                "cert_allocation": allocation,
            }
        )
    return {"status": "success", "date": req.date, "rows": results}


@router.post("/qc/dalian/update_certificate_no")
def update_dalian_certificate_no(req: QcDalianUpdateCertificateNoRequest):
    _ensure_order_details_loaded()
    row = _qc_resolve_order_detail_row(
        req.order_no,
        req.material_no,
        "",
        "",
        req.order_detail_id,
    )
    if not _is_dalian_order_no(req.order_no):
        raise HTTPException(status_code=400, detail="该订单不属于大连质检范围")
    item_no = req.item_no if req.item_no is not None else row.get("item_no")
    try:
        allocation = update_dalian_certificate_allocation(
            req.order_no,
            req.material_no,
            item_no,
            req.certificate_no,
            req.date,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "success",
        "order_detail_id": int(row["id"]),
        "certificate_no": allocation["certificate_no"],
        "cert_allocation": allocation,
    }


@router.post("/qc/update_delivery_qty")
def update_qc_delivery_qty(
    order_no: str = Form(...),
    material_no: str = Form(...),
    qty: int = Form(...),
    heat_no: str = Form(""),
    heat_treatment_batch_no: str = Form(""),
    order_detail_id: int | None = Form(None),
):
    row = _qc_resolve_order_detail_row(order_no, material_no, heat_no, heat_treatment_batch_no, order_detail_id)
    old_qty = int(row.get("quantity", 0) or 0)
    new_qty = int(qty)
    if old_qty == new_qty:
        return {"status": "success", "no_update": True, "old_qty": old_qty, "new_qty": new_qty}
    row["quantity"] = new_qty
    row["updated_at"] = now_iso()
    execute(
        "UPDATE order_details SET quantity=%s, updated_at=NOW() WHERE id=%s",
        (new_qty, int(row["id"])),
    )
    _append_log_line(
        f"qty_change_{datetime.now().strftime('%Y-%m-%d')}.log",
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} order={order_no} material={material_no} heat_no={heat_no} batch={heat_treatment_batch_no} old_qty={old_qty} new_qty={new_qty}",
    )
    return {"status": "success", "no_update": False, "old_qty": old_qty, "new_qty": new_qty}


def _has_packing_association_for_detail(row: dict) -> bool:
    packed = fetch_one(
        """
        SELECT id FROM packing_details
        WHERE order_no=%s AND material_no=%s AND COALESCE(entry_no, 0)=%s
        LIMIT 1
        """,
        (row["order_no"], row["material_no"], _item_no_key(row.get("item_no"))),
    )
    return packed is not None


@router.get("/orders/detail/{detail_id}/delete-check")
def delete_order_detail_check(detail_id: int):
    _ensure_order_details_loaded()
    row = next((r for r in order_details if int(r["id"]) == int(detail_id)), None)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到要删除的订单明细")
    return {
        "status": "success",
        "has_packing_association": _has_packing_association_for_detail(row),
    }


@router.delete("/orders/detail/{detail_id}")
def delete_order_detail(detail_id: int):
    _ensure_order_details_loaded()
    idx = next((i for i, r in enumerate(order_details) if int(r["id"]) == int(detail_id)), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="未找到要删除的订单明细")
    row = order_details[idx]
    has_packing_association = _has_packing_association_for_detail(row)
    execute("DELETE FROM order_details WHERE id=%s", (detail_id,))
    order_details.pop(idx)
    packing_deleted_count = _clear_packing_if_key_orphaned(
        row["order_no"],
        row["material_no"],
        row.get("item_no"),
    )
    log_payload = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": "delete",
        "deleted_id": detail_id,
        "has_packing_association": has_packing_association,
        "packing_deleted_count": packing_deleted_count,
        "snapshot": row,
    }
    _append_log_line(f"order_detail_change_{datetime.now().strftime('%Y-%m-%d')}.log", json.dumps(log_payload, ensure_ascii=False, default=str) + "\n")
    message = f"已删除 order_details 表中 id 为 {detail_id} 的 1 条数据"
    if packing_deleted_count:
        message += f"，并同步清除装箱记录 {packing_deleted_count} 条"
    return {
        "status": "success",
        "deleted_id": detail_id,
        "packing_deleted_count": packing_deleted_count,
        "message": message,
    }


@router.post("/qc/save")
def save_qc(req: QcSaveRequest):
    _ensure_order_details_loaded()
    _ensure_quality_records_loaded()
    row = _qc_resolve_order_detail_row(req.order_no, req.material_no, req.heat_no, req.heat_treatment_batch_no, req.order_detail_id)

    chemical_analysis = _normalize_chemical_analysis_rows(req.chemical_analysis)
    base_info = dict(req.base_info)
    cert_date_yymmdd: str | None = None
    cert_daily_seq: int | None = None
    item_no_key = normalize_item_no_key(row.get("item_no"))
    certificate_type: str | None = None
    order_detail_id = int(row["id"]) if row.get("id") is not None else req.order_detail_id

    if _is_dalian_qc_record(base_info):
        certificate_type = "dalian"
        date_text = _normalize_str(base_info.get("date", "")) or datetime.now().strftime("%Y-%m-%d")
        client_no = _normalize_str(base_info.get("certificate_no", ""))
        if not client_no:
            raise HTTPException(status_code=400, detail="请先确认出厂日期并分配证书编号")
        parsed = parse_certificate_no(client_no)
        if not parsed:
            raise HTTPException(status_code=400, detail="证书编号格式无效，应为 ZYXMZYYMMDD-N")
        cert_yymmdd, cert_seq = parsed
        if cert_yymmdd != yymmdd_from_date_text(date_text):
            raise HTTPException(status_code=400, detail="证书编号中的日期与出厂日期不一致")
        allocation = allocate_dalian_certificate(
            req.order_no,
            req.material_no,
            row.get("item_no"),
            date_text,
            force_regenerate=False,
        )
        if client_no != allocation["certificate_no"]:
            try:
                allocation = update_dalian_certificate_allocation(
                    req.order_no,
                    req.material_no,
                    row.get("item_no"),
                    client_no,
                    date_text,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        base_info["certificate_no"] = allocation["certificate_no"]
        cert_date_yymmdd = allocation["cert_date_yymmdd"]
        cert_daily_seq = int(allocation["cert_daily_seq"])

    rec_id = execute(
        """
        INSERT INTO qc_records (
            order_no, material_no, base_info, delivery_content, mechanical_tests, chemical_analysis,
            certificate_path, order_detail_id, item_no, item_no_key, cert_date_yymmdd, cert_daily_seq, certificate_type
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            req.order_no,
            req.material_no,
            json.dumps(base_info, ensure_ascii=False),
            json.dumps(req.delivery_content, ensure_ascii=False),
            json.dumps(req.mechanical_tests, ensure_ascii=False),
            json.dumps(chemical_analysis, ensure_ascii=False),
            "",
            order_detail_id,
            row.get("item_no"),
            item_no_key,
            cert_date_yymmdd,
            cert_daily_seq,
            certificate_type,
        ),
    )
    record = {
        "id": rec_id,
        "order_no": req.order_no,
        "material_no": req.material_no,
        "base_info": base_info,
        "delivery_content": req.delivery_content,
        "mechanical_tests": req.mechanical_tests,
        "chemical_analysis": chemical_analysis,
        "order_detail_id": order_detail_id,
        "item_no": row.get("item_no"),
        "item_no_key": item_no_key,
        "cert_date_yymmdd": cert_date_yymmdd,
        "cert_daily_seq": cert_daily_seq,
        "certificate_type": certificate_type,
        "created_at": now_iso(),
    }
    quality_records[rec_id] = record
    return {"status": "success", "record_id": rec_id, "record": record}


def _qc_cert_text(v: Any) -> str:
    text = _normalize_str(v)
    return text if text else "/"


def _qc_cert_number(v: Any) -> str:
    text = _format_three_decimals(v)
    return text if text else "/"


def _qc_cert_pair(left: Any, right: Any) -> str:
    return f"{_qc_cert_number(left)} / {_qc_cert_number(right)}"


def _qc_append_ut_mt_pt_mapping(
    mapping: dict[str, Any],
    material_no: str,
    drawing_no: str,
    *,
    cert_type: str = "",
) -> None:
    if cert_type == "A105_UTMTPT":
        mapping["ut"] = ""
        mapping["mt"] = ""
        mapping["pt"] = ""
        return
    values = _lookup_material_ut_mt_pt(material_no, drawing_no)
    mapping["ut"] = _qc_cert_text(values.get("ut", ""))
    mapping["mt"] = _qc_cert_text(values.get("mt", ""))
    mapping["pt"] = _qc_cert_text(values.get("pt", ""))


def _qc_region_dir(region: str) -> str:
    return "10-DL-qc" if _normalize_str(region).lower() in ("dalian", "10-dl-qc", "大连") else "11-FR-qc"


def _build_qc_render_mapping(record: dict, region: str, cert_type: str) -> dict[str, str]:
    """按 03-4-3 映射表 I 列从质检记录生成占位符字典（单条/批量生成共用）。"""
    region_dir = _qc_region_dir(region)
    return build_certificate_mapping(
        record,
        region=region_dir,
        cert_type=cert_type,
        cert_text=_qc_cert_text,
        cert_number=_qc_cert_number,
        spec_display_fn=_spec_to_certificate_display,
        same_as_a105=cert_same_as_a105(region_dir, cert_type),
    )


def _qc_finalize_certificate_output(
    docx_path: Path,
    download_name: str,
    output_format: str,
) -> tuple[Path, str, str]:
    fmt = _normalize_str(output_format).lower()
    if fmt != "pdf":
        return docx_path, download_name, _MEDIA_DOCX
    if _qc_pdf_render_mode() == "direct":
        raise HTTPException(
            status_code=500,
            detail=(
                "PDF 直填模式未生成文件，且未回退 Word/LibreOffice 转换；"
                "请检查 PDF 模板是否存在，或将 QC_PDF_RENDER_MODE 设为 word"
            ),
        )
    pdf_name = re.sub(r"\.docx?$", ".pdf", download_name, flags=re.IGNORECASE)
    pdf_path = docx_path.with_suffix(".pdf")
    convert_docx_to_pdf(docx_path, pdf_path)
    return pdf_path, pdf_name, _MEDIA_PDF


@router.post("/qc/generate_certificate")
def generate_quality_certificate(
    record_id: int = Form(...),
    output_format: str = Form("word"),
    use_utmtpt: str = Form("0"),
):
    _ensure_quality_records_loaded()
    record = quality_records.get(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="质检记录不存在")
    record = dict(record)
    record["chemical_analysis"] = _normalize_chemical_analysis_rows(record.get("chemical_analysis"))

    base = record.get("base_info", {})
    delivery_rows = record.get("delivery_content", [])
    delivery = delivery_rows[0] if delivery_rows else {}
    material_text = _normalize_str(base.get("material", ""))
    use_ut = _form_bool(use_utmtpt)
    cert_type = _resolve_qc_certificate_type(
        material_text,
        _normalize_str(record.get("material_no", "")),
        _normalize_str(delivery.get("drawing_no", "")),
        use_utmtpt=use_ut,
    )
    template = _resolve_qc_template("france", "word", cert_type)
    out_file: Path | None = None
    media_type = _MEDIA_DOCX
    try:
        item_no_text = _normalize_str(delivery.get("item_no", ""))
        heat_no_text = _normalize_str(delivery.get("raw_material_no", ""))
        file_name = f"{record['material_no']}_{record['order_no']}_{item_no_text}_3-1_{heat_no_text}.docx"
        mapping = _build_qc_render_mapping(record, "france", cert_type)
        _qc_append_ut_mt_pt_mapping(
            mapping,
            record.get("material_no", ""),
            delivery.get("drawing_no", ""),
            cert_type=cert_type,
        )
        if _normalize_str(output_format).lower() == "pdf":
            direct = _attempt_qc_pdf_direct_render(
                "france",
                cert_type,
                mapping,
                file_name,
                record_id=record_id,
                region_label="france",
            )
            if direct:
                out_file, file_name, media_type = direct
                _persist_generated_certificate(record_id, file_name)
                return _file_download_response(out_file, file_name, media_type)

        out_file = _make_temp_output_path(".docx")
        mech = _qc_mechanical_rows_to_certificate_values(record.get("mechanical_tests", []))
        chem_raw, chem_self = _qc_chemical_analysis_to_certificate_values(record.get("chemical_analysis", []))
        spec_for_certificate = _spec_to_certificate_display(delivery.get("specifications", ""))
        append_blocks = None
        if not template.exists():
            append_blocks = [
                "质量证明书 / Quality Certificate",
                f"证书编号: {_qc_cert_text(base.get('certificate_no', ''))}",
                f"客户名称: {_qc_cert_text(base.get('customer', ''))}",
                f"合同号: {_qc_cert_text(base.get('contract_no', ''))}",
                f"出厂日期: {_qc_cert_text(base.get('date', ''))}",
                f"交货状态: {_qc_cert_text(base.get('state_of_delivery', ''))}",
                f"材料: {_qc_cert_text(base.get('material', ''))}",
                f"数量: {_qc_cert_text(delivery.get('qty', ''))}",
                f"图纸编号: {_qc_cert_text(delivery.get('drawing_no', ''))}",
                f"零件号: {_qc_cert_text(delivery.get('part_no', ''))}",
                f"规格: {_qc_cert_text(spec_for_certificate)}",
                f"原材料炉号: {_qc_cert_text(heat_no_text)}",
                f"热处理批号: {_qc_cert_text(delivery.get('batch_no', ''))}",
                "",
                "机械测试",
                f"测试温度: {_qc_cert_text(mech.get('test_temp', ''))}",
                f"屈服强度: {_qc_cert_text(mech.get('yield_strength', ''))}",
                f"抗拉强度: {_qc_cert_text(mech.get('tensile_strength', ''))}",
                f"伸长率: {_qc_cert_text(mech.get('elongation', ''))}",
                f"断面收缩率: {_qc_cert_text(mech.get('reduction_area', ''))}",
                f"硬度: {_qc_cert_text(mech.get('hardness', ''))}",
                f"冲击试验: {_qc_cert_text(mech.get('impact_test', ''))}",
                "",
                "化学分析（原材 / 自检）",
                f"C: {_qc_cert_pair(chem_raw.get('C'), chem_self.get('C'))}",
                f"Mn: {_qc_cert_pair(chem_raw.get('Mn'), chem_self.get('Mn'))}",
                f"P: {_qc_cert_pair(chem_raw.get('P'), chem_self.get('P'))}",
                f"S: {_qc_cert_pair(chem_raw.get('S'), chem_self.get('S'))}",
                f"Si: {_qc_cert_pair(chem_raw.get('Si'), chem_self.get('Si'))}",
                f"Cu: {_qc_cert_pair(chem_raw.get('Cu'), chem_self.get('Cu'))}",
                f"Ni: {_qc_cert_pair(chem_raw.get('Ni'), chem_self.get('Ni'))}",
                f"Cr: {_qc_cert_pair(chem_raw.get('Cr'), chem_self.get('Cr'))}",
                f"Mo: {_qc_cert_pair(chem_raw.get('Mo'), chem_self.get('Mo'))}",
                f"V: {_qc_cert_pair(chem_raw.get('V'), chem_self.get('V'))}",
                f"CEQ: {_qc_cert_pair(chem_raw.get('CEQ'), chem_self.get('CEQ'))}",
            ]
        render_docx_template(template, out_file, mapping, append_blocks=append_blocks)
        fit_qc_certificate_to_single_page(out_file)
        final_path, final_name, media_type = _qc_finalize_certificate_output(out_file, file_name, output_format)
        if final_path != out_file:
            try:
                out_file.unlink(missing_ok=True)
            except OSError:
                pass
            out_file = final_path
            file_name = final_name
    except (HTTPException, Exception) as exc:
        if out_file is not None:
            try:
                out_file.unlink(missing_ok=True)
            except OSError:
                pass
        failure_context = {
            "order_no": record.get("order_no", ""),
            "material_no": record.get("material_no", ""),
            "template": str(template),
            "template_exists": template.exists(),
            "cert_type": cert_type,
            "output_format": output_format,
        }
        log_path = _log_qc_certificate_failure("france", record_id, exc, context=failure_context)
        if isinstance(exc, HTTPException):
            if isinstance(exc.detail, str) and "详见日志" not in exc.detail:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=f"{exc.detail}（详见日志 {log_path}）",
                ) from exc
            raise
        raise HTTPException(status_code=500, detail=f"质保书生成失败: {exc}（详见日志 {log_path}）") from exc

    _persist_generated_certificate(record_id, file_name)
    return _file_download_response(out_file, file_name, media_type)


@router.post("/qc/dalian/generate_certificate")
def generate_dalian_quality_certificate(
    record_id: int = Form(...),
    output_format: str = Form("word"),
    use_utmtpt: str = Form("0"),
):
    _ensure_quality_records_loaded()
    record = quality_records.get(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="质检记录不存在")
    record = dict(record)
    record["chemical_analysis"] = _normalize_chemical_analysis_rows(record.get("chemical_analysis"))

    base = record.get("base_info", {})
    delivery_rows = record.get("delivery_content", [])
    delivery = delivery_rows[0] if delivery_rows else {}
    material_text = _normalize_str(base.get("material", ""))
    use_ut = _form_bool(use_utmtpt)
    cert_type = _resolve_qc_certificate_type(
        material_text,
        _normalize_str(record.get("material_no", "")),
        _normalize_str(delivery.get("drawing_no", "")),
        use_utmtpt=use_ut,
    )
    template = _resolve_qc_template("dalian", "word", cert_type)
    out_file: Path | None = None
    media_type = _MEDIA_DOCX
    try:
        order_no_text = _normalize_str(record.get("order_no", ""))
        heat_no_text = _normalize_str(delivery.get("raw_material_no", ""))
        name_text = _sanitize_file_name_part(delivery.get("name", ""))
        file_name = f"{_sanitize_file_name_part(order_no_text)}_{_sanitize_file_name_part(heat_no_text)}_{name_text}.docx"
        mapping = _build_qc_render_mapping(record, "dalian", cert_type)
        _qc_append_ut_mt_pt_mapping(
            mapping,
            record.get("material_no", ""),
            delivery.get("drawing_no", ""),
            cert_type=cert_type,
        )
        if _normalize_str(output_format).lower() == "pdf":
            direct = _attempt_qc_pdf_direct_render(
                "dalian",
                cert_type,
                mapping,
                file_name,
                record_id=record_id,
                region_label="dalian",
            )
            if direct:
                out_file, file_name, media_type = direct
                _persist_generated_certificate(record_id, file_name)
                return _file_download_response(out_file, file_name, media_type)

        out_file = _make_temp_output_path(".docx")
        min_group, max_group = _dalian_mech_groups_to_certificate_values(record.get("mechanical_tests", {}))
        spec_for_certificate = _spec_to_certificate_display(delivery.get("specifications", ""))
        append_blocks = None
        if not template.exists():
            append_blocks = [
                "大连质量证明书 / Dalian Quality Certificate",
                f"证书编号: {_qc_cert_text(base.get('certificate_no', ''))}",
                f"客户: {_qc_cert_text(base.get('customer', ''))}",
                f"合同号: {_qc_cert_text(base.get('contract_no', ''))}",
                f"日期: {_qc_cert_text(base.get('date', ''))}",
                f"材料: {_qc_cert_text(base.get('material', ''))}",
                f"交货状态: {_qc_cert_text(base.get('state_of_delivery', ''))}",
                f"数量: {_qc_cert_text(delivery.get('qty', ''))}",
                f"图纸: {_qc_cert_text(delivery.get('drawing_no', ''))}",
                f"零件号: {_qc_cert_text(delivery.get('part_no', ''))}",
                f"规格: {_qc_cert_text(spec_for_certificate)}",
                f"炉号: {_qc_cert_text(heat_no_text)}",
                "",
                "机械测试（最小组）",
                f"零件号: {_qc_cert_text(min_group.get('part_no', ''))}",
                f"规格: {_qc_cert_text(min_group.get('spec', ''))}",
                f"屈服: {_qc_cert_text(min_group.get('yield_strength', ''))}",
                f"抗拉: {_qc_cert_text(min_group.get('tensile_strength', ''))}",
                "",
                "机械测试（最大组）",
                f"零件号: {_qc_cert_text(max_group.get('part_no', ''))}",
                f"规格: {_qc_cert_text(max_group.get('spec', ''))}",
                f"屈服: {_qc_cert_text(max_group.get('yield_strength', ''))}",
                f"抗拉: {_qc_cert_text(max_group.get('tensile_strength', ''))}",
            ]
        render_docx_template(template, out_file, mapping, append_blocks=append_blocks)
        fit_qc_certificate_to_single_page(out_file)
        final_path, final_name, media_type = _qc_finalize_certificate_output(out_file, file_name, output_format)
        if final_path != out_file:
            try:
                out_file.unlink(missing_ok=True)
            except OSError:
                pass
            out_file = final_path
            file_name = final_name
    except (HTTPException, Exception) as exc:
        if out_file is not None:
            try:
                out_file.unlink(missing_ok=True)
            except OSError:
                pass
        delivery_ctx = (record.get("delivery_content") or [{}])[0]
        failure_context = {
            "order_no": record.get("order_no", ""),
            "material_no": record.get("material_no", ""),
            "template": str(template),
            "template_exists": template.exists(),
            "delivery_name": _normalize_str(delivery_ctx.get("name", "")),
            "heat_no": _normalize_str(delivery_ctx.get("raw_material_no", "")),
            "cert_type": cert_type,
            "output_format": output_format,
        }
        log_path = _log_qc_certificate_failure("dalian", record_id, exc, context=failure_context)
        if isinstance(exc, HTTPException):
            if isinstance(exc.detail, str) and "详见日志" not in exc.detail:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=f"{exc.detail}（详见日志 {log_path}）",
                ) from exc
            raise
        raise HTTPException(status_code=500, detail=f"大连质保书生成失败: {exc}（详见日志 {log_path}）") from exc

    _persist_generated_certificate(record_id, file_name)
    return _file_download_response(out_file, file_name, media_type)


@router.post("/packing/start")
def start_packing(req: PackingStartRequest):
    if not fetch_one("SELECT id FROM order_details WHERE order_no=%s LIMIT 1", (req.order_no,)):
        raise HTTPException(status_code=404, detail="订单不存在")
    _initialize_packing_rows(req.order_no)
    packing_sessions[req.order_no] = {"locked": True, "started_at": now_iso()}
    return {"status": "success", "order_no": req.order_no, "session": packing_sessions[req.order_no]}


@router.get("/packing/load")
def load_packing_rows(order_no: str):
    _ensure_order_details_loaded()
    _initialize_packing_rows(order_no)
    rows = _fetch_packing_rows_from_db(order_no)
    return {
        "status": "success",
        "factory_order_no": _get_factory_order_no(order_no),
        "rows": [_serialize_packing_row(r) for r in rows],
    }


@router.post("/packing/update_factory_order_no")
def update_packing_factory_order_no(req: UpdateFactoryOrderNoRequest):
    _ensure_order_details_loaded()
    target = [r for r in order_details if r["order_no"] == req.order_no]
    if not target:
        raise HTTPException(status_code=404, detail="订单不存在")
    previous = _normalize_str(target[0].get("factory_order_no", ""))
    current = _normalize_str(req.factory_order_no)
    if previous == current:
        return {"status": "success", "order_no": req.order_no, "factory_order_no": current, "updated_count": 0}
    execute(
        "UPDATE order_details SET factory_order_no=%s, updated_at=NOW() WHERE order_no=%s",
        (current or None, req.order_no),
    )
    nowv = now_iso()
    for row in target:
        row["factory_order_no"] = current
        row["updated_at"] = nowv
    _append_log_line(
        f"factory_order_no_change_{datetime.now().strftime('%Y-%m-%d')}.log",
        (
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
            f"order={req.order_no} old_factory_order_no={previous} new_factory_order_no={current}\n"
        ),
    )
    return {"status": "success", "order_no": req.order_no, "factory_order_no": current, "updated_count": len(target)}


@router.post("/packing/save")
def save_packing_rows(req: PackingSaveRequest):
    _ensure_order_details_loaded()
    if req.deleted_ids:
        execute_many("DELETE FROM packing_details WHERE id=%s", [(int(x),) for x in req.deleted_ids])
    current_rows = _fetch_packing_rows_from_db(req.order_no)
    id_map = {int(r["id"]): r for r in current_rows if r.get("id") is not None}
    for row in req.rows:
        total_weight = round(float(row.quantity) * float(row.unit_weight or 0), 2)
        if row.id is not None and int(row.id) in id_map:
            previous_item = id_map[int(row.id)].get("entry_no")
            next_item = int(row.item_no) if row.item_no is not None else None
            if previous_item != next_item:
                _append_log_line(
                    f"packing_item_change_{datetime.now().strftime('%Y-%m-%d')}.log",
                    (
                        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
                        f"order={req.order_no} material={row.material_no} row_id={int(row.id)} "
                        f"old_item={previous_item if previous_item is not None else ''} "
                        f"new_item={next_item if next_item is not None else ''}\n"
                    ),
                )
            execute(
                """
                UPDATE packing_details
                SET entry_no=%s, quantity=%s, unit_weight=%s, total_weight=%s, spec=%s, standard=%s, material=%s, remark1=%s, box_no=%s, updated_at=NOW()
                WHERE id=%s
                """,
                (
                    int(row.item_no) if row.item_no is not None else None,
                    int(row.quantity or 0),
                    round(float(row.unit_weight or 0), 2),
                    total_weight,
                    row.spec,
                    row.standard,
                    row.material,
                    row.remark1,
                    int(row.box_no or 0),
                    int(row.id),
                ),
            )
        else:
            execute(
                """
                INSERT INTO packing_details (
                    order_no, material_no, spec, standard, material, quantity, unit_weight, total_weight,
                    gross_weight, remark1, box_no, entry_no, box_length, box_width, box_height,
                    packing_remark, delivery_date, packed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    req.order_no,
                    row.material_no,
                    row.spec,
                    row.standard,
                    row.material,
                    int(row.quantity or 0),
                    round(float(row.unit_weight or 0), 2),
                    total_weight,
                    0.0,
                    row.remark1,
                    int(row.box_no or 0),
                    int(row.item_no) if row.item_no is not None else None,
                    0,
                    0,
                    0,
                    "",
                    None,
                    None,
                ),
            )

    touched_keys = {(r.material_no, _item_no_key(r.item_no)) for r in req.rows}
    for row in order_details:
        if row["order_no"] == req.order_no and (row["material_no"], _item_no_key(row.get("item_no"))) in touched_keys:
            row["status"] = "包装"
            row["updated_at"] = now_iso()
    if touched_keys:
        execute_many(
            """
            UPDATE order_details
            SET order_status=%s, updated_at=NOW()
            WHERE order_no=%s AND material_no=%s AND COALESCE(item_no, 0)=%s
            """,
            [("包装", req.order_no, material_no, item_key) for material_no, item_key in touched_keys],
        )

    _reload_packing_details_cache()
    saved_rows = _fetch_packing_rows_from_db(req.order_no)
    return {"status": "success", "rows": [_serialize_packing_row(r) for r in saved_rows]}


@router.get("/packing/material")
def get_material_for_packing(order_no: str, material_no: str):
    rows = [r for r in order_details if r["order_no"] == order_no and r["material_no"] == material_no]
    if not rows:
        raise HTTPException(status_code=404, detail="未检索到该订单/物料")
    now = now_iso()
    for r in rows:
        r["status"] = "装箱"
        r["updated_at"] = now
    base = rows[0]
    return {
        "status": "success",
        "row": {
            "material_no": base["material_no"],
            "standard": base["standard"],
            "spec": base["spec"],
            "material": base["material"],
            "quantity": base["quantity"],
            "unit_weight": base["unit_weight"],
            "total_weight": base["total_weight"],
            "remark": base["remark1"],
        },
    }


@router.post("/packing/submit")
def submit_packing(req: PackingSubmitRequest):
    _ensure_order_details_loaded()
    current_rows = _fetch_packing_rows_from_db(req.order_no)
    if req.order_no not in packing_sessions:
        rows = _find_order(req.order_no)
        if not rows:
            raise HTTPException(status_code=404, detail="订单不存在")
        packing_sessions[req.order_no] = {"locked": True, "started_at": now_iso()}
    if not req.items:
        raise HTTPException(status_code=400, detail="请先勾选要入箱的物料+条目")
    id_map = {int(r["id"]): r for r in current_rows if r.get("id") is not None}
    invalid_box = [item.row_id for item in req.items if int(item.box_no) < 1]
    if invalid_box:
        raise HTTPException(status_code=400, detail="所选行箱号须为大于0的整数，请先在Step2填写箱号")
    missing = [item.row_id for item in req.items if int(item.row_id) not in id_map]
    if missing:
        raise HTTPException(status_code=404, detail="未找到需要装箱提交的条目，请先保存Step2数据")

    now = now_iso()
    execute_many(
        """
        UPDATE packing_details
        SET box_no=%s, box_length=%s, box_width=%s, box_height=%s, packing_remark=%s, gross_weight=%s, packed_at=%s, updated_at=NOW()
        WHERE id=%s
        """,
        [
            (
                int(item.box_no),
                req.box_length,
                req.box_width,
                req.box_height,
                req.packing_remark,
                round(float(req.gross_weight), 2),
                now,
                int(item.row_id),
            )
            for item in req.items
        ],
    )

    _reload_packing_details_cache()
    rows = _fetch_packing_rows_from_db(req.order_no)
    return {"status": "success", "count": len(req.items), "rows": [_serialize_packing_row(r) for r in rows]}


@router.post("/packing/finish")
def finish_packing(req: PackingFinishRequest):
    _ensure_order_details_loaded()
    rows = _find_order(req.order_no)
    if not rows:
        raise HTTPException(status_code=404, detail="订单不存在")
    invalid = [r for r in rows if r["status"] not in ("装箱", "打包", "发货", "完成")]
    if invalid:
        material_nos = [r["material_no"] for r in invalid]
        return {
            "status": "warning",
            "message": f"请确认物料号：{','.join(material_nos)}的装箱情况",
            "material_nos": material_nos,
        }
    done_time = now_iso()
    packing_sessions.setdefault(req.order_no, {"locked": True, "started_at": done_time})["finished_at"] = done_time
    return {"status": "success", "order_no": req.order_no, "finished_at": done_time}


@router.post("/packing/generate_list")
def generate_packing_list(req: GeneratePackingListRequest):
    _ensure_order_details_loaded()
    if Workbook is None:
        raise HTTPException(status_code=500, detail="缺少openpyxl依赖，无法生成装箱单。")
    rows = [r for r in _fetch_packing_rows_from_db(req.order_no) if int(r.get("box_no", 0) or 0) > 0]
    if not rows:
        raise HTTPException(status_code=404, detail="装箱明细为空")

    now = now_local_compact()
    factory_order_no = _normalize_str(req.factory_order_no) or _get_factory_order_no(req.order_no)
    file_name = f"【装箱单】{factory_order_no}_{req.order_no}_{now}.xlsx"
    out_file = _make_temp_output_path(".xlsx")

    template = TEMPLATES_DIR / "装箱单模板.xlsx"
    try:
        wb = load_workbook(template) if template.exists() else Workbook()
        ws = wb.active
        if not template.exists():
            ws["A1"] = f"订单号：{factory_order_no} {req.order_no} 装箱单"
            ws["A2"] = "箱号"
            ws["B2"] = "条目"
            ws["C2"] = "R3P物料号"
            ws["D2"] = "标准"
            ws["E2"] = "规格"
            ws["F2"] = "材质"
            ws["G2"] = "数量"
            ws["H2"] = "单重"
            ws["I2"] = "总重"
            ws["J2"] = "备注"
            ws["K2"] = "尺寸-长"
            ws["L2"] = "尺寸-宽"
            ws["M2"] = "尺寸-高"
            ws["N2"] = "装箱备注"
        else:
            ws["A1"] = f"订单号：{factory_order_no} {req.order_no} 装箱单"

        ordered_rows = sorted(rows, key=lambda x: (int(x.get("box_no", 0)), int(x.get("entry_no", 0))))
        box_meta: dict[int, dict[str, Any]] = {}
        for row in ordered_rows:
            box_no = int(row.get("box_no", 0) or 0)
            meta = box_meta.setdefault(
                box_no,
                {"box_length": "", "box_width": "", "box_height": "", "packing_remark": ""},
            )
            for key in ("box_length", "box_width", "box_height"):
                if not meta[key] and row.get(key):
                    meta[key] = row.get(key)
            if not meta["packing_remark"] and _normalize_str(row.get("packing_remark")):
                meta["packing_remark"] = _normalize_str(row.get("packing_remark"))
        template_style_row = 4 if ws.max_row >= 4 else None
        current_row = 4
        merge_targets: list[tuple[int, int]] = []
        current_box = None
        group_start = 4

        def _copy_row_style(src_row: int, dst_row: int):
            for col in range(1, 15):
                src = ws.cell(src_row, col)
                dst = ws.cell(dst_row, col)
                dst._style = copy(src._style)
                dst.font = copy(src.font)
                dst.fill = copy(src.fill)
                dst.border = copy(src.border)
                dst.alignment = copy(src.alignment)
                dst.number_format = src.number_format
                dst.protection = copy(src.protection)

        for idx, r in enumerate(ordered_rows):
            if template_style_row and current_row != template_style_row:
                ws.insert_rows(current_row, 1)
                _copy_row_style(template_style_row, current_row)
            elif template_style_row:
                _copy_row_style(template_style_row, current_row)

            box_no = int(r.get("box_no", 0) or 0)
            if current_box is None:
                current_box = box_no
                group_start = current_row
            elif current_box != box_no:
                merge_targets.append((group_start, current_row - 1))
                current_box = box_no
                group_start = current_row

            first_in_box = current_row == group_start
            current_meta = box_meta.get(box_no, {})
            ws.cell(current_row, 1).value = box_no if first_in_box else None
            ws.cell(current_row, 2).value = r.get("entry_no", "")
            ws.cell(current_row, 3).value = r.get("material_no", "")
            ws.cell(current_row, 4).value = r.get("standard", "")
            ws.cell(current_row, 5).value = r.get("spec", "")
            ws.cell(current_row, 6).value = r.get("material", "")
            ws.cell(current_row, 7).value = r.get("quantity", 0)
            ws.cell(current_row, 8).value = r.get("unit_weight", 0)
            ws.cell(current_row, 9).value = r.get("total_weight", 0)
            ws.cell(current_row, 10).value = r.get("remark1", "")
            ws.cell(current_row, 11).value = current_meta.get("box_length", "") if first_in_box else None
            ws.cell(current_row, 12).value = current_meta.get("box_width", "") if first_in_box else None
            ws.cell(current_row, 13).value = current_meta.get("box_height", "") if first_in_box else None
            ws.cell(current_row, 14).value = current_meta.get("packing_remark", "") if first_in_box else None
            current_row += 1

        if ordered_rows:
            merge_targets.append((group_start, current_row - 1))

        for start, end in merge_targets:
            if start < end:
                for col in ("A", "K", "L", "M", "N"):
                    ws.merge_cells(f"{col}{start}:{col}{end}")

        summary_row = current_row
        style_source_row = summary_row - 1 if summary_row > 4 else None
        if style_source_row:
            _copy_row_style(style_source_row, summary_row)
        ws.cell(summary_row, 1).value = "合计："
        ws.cell(summary_row, 7).value = sum(int(r.get("quantity", 0)) for r in ordered_rows)
        ws.cell(summary_row, 9).value = round(sum(float(r.get("total_weight", 0)) for r in ordered_rows), 2)
        wb.save(out_file)

        touched = {r["material_no"] for r in ordered_rows}
        for row in order_details:
            if row["order_no"] == req.order_no and row["material_no"] in touched:
                row["status"] = "装箱"
                row["updated_at"] = now_iso()
        execute_many(
            "UPDATE order_details SET order_status=%s, updated_at=NOW() WHERE order_no=%s AND material_no=%s",
            [("装箱", req.order_no, material_no) for material_no in touched],
        )
    except Exception as exc:
        try:
            out_file.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"装箱单生成失败: {exc}") from exc

    return _file_download_response(out_file, file_name, _MEDIA_XLSX)


@router.post("/shipping/generate_mark")
def generate_shipping_mark(req: GenerateShippingMarkRequest):
    rows = _fetch_packing_rows_from_db(req.order_no)
    chosen = set(int(x) for x in req.box_nos) if req.box_nos else set()
    if req.box_no is not None:
        chosen.add(int(req.box_no))
    if chosen:
        rows = [r for r in rows if int(r["box_no"]) in chosen]
    if not rows:
        raise HTTPException(status_code=404, detail="无可生成的装箱明细")
    now = now_local_compact()
    box_suffix = f"_{req.box_no}" if req.box_no is not None else ""
    file_name = f"【唛头】{req.order_no}{box_suffix}_{now}.docx"
    out_file: Path | None = None

    if Document is None or WD_ALIGN_PARAGRAPH is None or Cm is None or Pt is None or qn is None:
        raise HTTPException(status_code=500, detail="缺少python-docx依赖，无法生成唛头。")

    try:
        out_file = _make_temp_output_path(".docx")
        grouped: dict[int, list[dict]] = {}
        for r in rows:
            grouped.setdefault(int(r["box_no"]), []).append(r)

        total_boxes = len({int(r["box_no"]) for r in _fetch_packing_rows_from_db(req.order_no)})
        doc = Document()
        section = doc.sections[0]
        section.page_width = Cm(max(float(req.width_cm or 10), 1))
        section.page_height = Cm(max(float(req.length_cm or 10), 1))
        section.top_margin = Cm(1)
        section.bottom_margin = Cm(1)
        section.left_margin = Cm(1)
        section.right_margin = Cm(1)

        def _set_run_font(run, size_pt: int) -> None:
            run.font.name = "SimSun"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")
            run.font.size = Pt(size_pt)

        def _pick_font_size() -> int:
            return max(int(req.font_size or 12), 1)

        def _render_detail_line(template_text: str, item: dict) -> list[str]:
            mapping = {
                "po_no": item.get("order_no", ""),
                "item_no": item.get("entry_no", ""),
                "material_no": item.get("material_no", ""),
                "standard": item.get("standard", ""),
                "spec": item.get("spec", ""),
                "dn": item.get("spec", ""),
                "qty": item.get("quantity", 0),
                "net_weight": item.get("total_weight", 0),
            }
            text = template_text
            for key, value in mapping.items():
                text = text.replace(f"{{{{{key}}}}}", str(value))
            return text.splitlines() or [text]

        for box_no in sorted(grouped.keys()):
            items = sorted(grouped[box_no], key=lambda x: int(x.get("entry_no", 0)))
            if not all(_normalize_str(it.get("delivery_date")) for it in items):
                raise HTTPException(status_code=400, detail=f"箱号{box_no}未设置交货日期，无法生成唛头")

            detail_lines: list[str] = []
            for it in items:
                detail_lines.extend(_render_detail_line(req.detail_format, it))
            tail_lines = [
                f"Gross weight：{round(float(items[0].get('gross_weight', 0)) + sum(float(i.get('total_weight', 0)) for i in items), 2)} KG",
                f"Part No.：{total_boxes}-{box_no}",
                f"Delivery date：{items[0].get('delivery_date', '')}",
            ]
            font_size = _pick_font_size()

            if doc.paragraphs:
                doc.add_page_break()

            title_paragraph = doc.add_paragraph()
            title_paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            title_paragraph.paragraph_format.left_indent = Pt(0)
            title_paragraph.paragraph_format.first_line_indent = Pt(font_size * 8)
            title_run = title_paragraph.add_run(req.title or "ZHANGQIU MINGYUAN MACHINERY CO.,LTD")
            _set_run_font(title_run, font_size)

            for line in detail_lines + tail_lines:
                para = doc.add_paragraph()
                para.alignment = WD_ALIGN_PARAGRAPH.LEFT
                para.paragraph_format.left_indent = Pt(font_size)
                para.paragraph_format.first_line_indent = Pt(0)
                run = para.add_run(line)
                _set_run_font(run, font_size)

        doc.save(out_file)
    except HTTPException as exc:
        if out_file is not None:
            try:
                out_file.unlink(missing_ok=True)
            except OSError:
                pass
        raise exc
    except Exception as exc:
        if out_file is not None:
            try:
                out_file.unlink(missing_ok=True)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail=f"唛头生成失败: {exc}") from exc

    return _file_download_response(out_file, file_name, _MEDIA_DOCX)


@router.get("/shipping/boxes")
def list_shipping_boxes(order_no: str):
    _ensure_packing_details_loaded()
    rows = [r for r in packing_details if r["order_no"] == order_no]
    grouped: dict[int, dict] = {}
    for r in rows:
        box_no = int(r["box_no"])
        g = grouped.setdefault(
            box_no,
            {
                "box_no": box_no,
                "gross_weight": float(r.get("gross_weight", 0)),
                "delivery_date": _normalize_str(r.get("delivery_date", "")),
            },
        )
        if not g["delivery_date"] and _normalize_str(r.get("delivery_date", "")):
            g["delivery_date"] = _normalize_str(r.get("delivery_date", ""))
    return {"status": "success", "rows": sorted(grouped.values(), key=lambda x: x["box_no"])}


@router.post("/shipping/update_delivery_dates")
def update_shipping_delivery_dates(req: UpdateDeliveryDateRequest):
    _ensure_packing_details_loaded()
    updated = 0
    target = {int(x.box_no): x.delivery_date for x in req.rows}
    for r in packing_details:
        if r["order_no"] != req.order_no:
            continue
        box_no = int(r["box_no"])
        if box_no in target:
            r["delivery_date"] = target[box_no]
            updated += 1
    execute_many(
        "UPDATE packing_details SET delivery_date=%s, updated_at=NOW() WHERE order_no=%s AND box_no=%s",
        [(delivery_date, req.order_no, box_no) for box_no, delivery_date in target.items()],
    )
    return {"status": "success", "updated_count": updated}


@router.post("/shipping/advance_status")
def advance_shipping_status(req: AdvanceShippingStatusRequest):
    _ensure_packing_details_loaded()
    _ensure_order_details_loaded()
    if req.target_status not in ("打包", "发货"):
        raise HTTPException(status_code=400, detail="仅支持转入打包或确认发货")
    rows = [r for r in packing_details if r["order_no"] == req.order_no]
    if not rows:
        raise HTTPException(status_code=404, detail="未找到装箱明细")
    missing = sorted({int(r["box_no"]) for r in rows if not _normalize_str(r.get("delivery_date", ""))})
    if missing:
        raise HTTPException(status_code=400, detail=f"箱号{','.join(str(x) for x in missing)}未设置交货日期")
    nowv = now_iso()
    for r in order_details:
        if r["order_no"] == req.order_no:
            r["status"] = req.target_status
            r["updated_at"] = nowv
            if req.target_status == "打包":
                r["packed_at"] = nowv
            if req.target_status == "发货":
                r["shipped_at"] = nowv
    execute_many(
        """
        UPDATE order_details
        SET order_status=%s, packed_at=%s, shipped_at=%s, updated_at=NOW()
        WHERE id=%s
        """,
        [
            (
                req.target_status,
                nowv if req.target_status == "打包" else r.get("packed_at") or None,
                nowv if req.target_status == "发货" else r.get("shipped_at") or None,
                int(r["id"]),
            )
            for r in order_details
            if r["order_no"] == req.order_no
        ],
    )
    return {"status": "success", "order_no": req.order_no, "target_status": req.target_status, "updated_at": nowv}


@router.get("/shipping/dalian/details")
def get_dalian_shipping_details(delivery_date: str):
    target_date = _validate_delivery_date_yyyymmdd(delivery_date)
    rows = _fetch_dalian_rows_by_date(target_date)
    return {"status": "success", "delivery_date": target_date, "rows": rows}


@router.get("/shipping/dalian/max-id")
def get_dalian_shipping_max_id():
    return {"status": "success", "max_id": _get_dalian_max_id()}


@router.post("/shipping/dalian/row/save")
def save_dalian_shipping_row(req: DalianShippingSaveRowRequest):
    target_date = _validate_delivery_date_yyyymmdd(req.delivery_date)
    normalized = _normalize_dalian_row(req.row, target_date)
    if not normalized["order_no"]:
        raise HTTPException(status_code=400, detail="订单号不能为空")
    if not normalized["item_no"]:
        raise HTTPException(status_code=400, detail="条目不能为空")

    row_id = req.row.id
    try:
        if row_id:
            existing = fetch_one("SELECT * FROM dalian_shipping_details WHERE id=%s", (int(row_id),))
            if not existing:
                raise HTTPException(status_code=404, detail=f"未找到 id={row_id} 的发货明细")
            if _dalian_rows_equal(existing, normalized):
                return {
                    "status": "success",
                    "no_update": True,
                    "message": "无更新",
                    "row": _serialize_dalian_db_row(existing),
                }
            saved = _update_dalian_row(int(row_id), normalized)
            return {"status": "success", "no_update": False, "message": "保存成功", "row": saved}
        saved = _insert_dalian_row(normalized, req.created_by)
        return {"status": "success", "no_update": False, "message": "保存成功", "row": saved}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存失败: {exc}") from exc


@router.delete("/shipping/dalian/row/{row_id}")
def delete_dalian_shipping_row(row_id: int):
    existing = fetch_one("SELECT id FROM dalian_shipping_details WHERE id=%s", (row_id,))
    if not existing:
        raise HTTPException(status_code=404, detail=f"未找到 id={row_id} 的发货明细")
    execute("DELETE FROM dalian_shipping_details WHERE id=%s", (row_id,))
    return {
        "status": "success",
        "deleted_count": 1,
        "message": f"已删除 dalian_shipping_details 表中 id 为 {row_id} 的 1 条数据",
    }


@router.post("/shipping/dalian/submit")
def submit_dalian_shipping_details(req: DalianShippingSubmitRequest):
    target_date = _validate_delivery_date_yyyymmdd(req.delivery_date)
    if not req.rows:
        raise HTTPException(status_code=400, detail="没有可提交的可编辑发货明细")

    normalized_rows = [_normalize_dalian_row(row, target_date) for row in req.rows]
    for idx, row in enumerate(normalized_rows, start=1):
        if not row["order_no"]:
            raise HTTPException(status_code=400, detail=f"第{idx}行订单号不能为空")
        if not row["item_no"]:
            raise HTTPException(status_code=400, detail=f"第{idx}行条目不能为空")

    saved_ids: list[int] = []
    try:
        for row_model, normalized in zip(req.rows, normalized_rows):
            if row_model.id:
                existing = fetch_one(
                    "SELECT * FROM dalian_shipping_details WHERE id=%s",
                    (int(row_model.id),),
                )
                if existing:
                    if _dalian_rows_equal(existing, normalized):
                        saved_ids.append(int(row_model.id))
                        continue
                    saved = _update_dalian_row(int(row_model.id), normalized)
                    saved_ids.append(int(saved["id"]))
                    continue
            saved = _insert_dalian_row(normalized, req.created_by)
            saved_ids.append(int(saved["id"]))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"更新失败: {exc}") from exc

    saved_rows: list[dict[str, Any]] = []
    for rid in saved_ids:
        row = _fetch_dalian_row_by_id(rid)
        if row:
            saved_rows.append(row)
    return {
        "status": "success",
        "delivery_date": target_date,
        "saved_count": len(saved_rows),
        "saved_ids": saved_ids,
        "rows": saved_rows,
        "message": f"更新成功，共 {len(saved_rows)} 条，ID：{', '.join(str(i) for i in saved_ids)}",
    }


@router.post("/shipping/dalian/generate_mark")
def generate_dalian_shipping_mark(req: DalianShippingGenerateMarkRequest):
    target_date = _validate_delivery_date_yyyymmdd(req.delivery_date)
    export_rows = _fetch_dalian_rows_by_date(target_date)

    if not export_rows:
        raise HTTPException(status_code=404, detail=f"未找到发货日期 {target_date} 的发货明细，请先提交发货明细后再生成唛头")

    formatted_date = _format_dalian_mark_date(target_date)
    file_name = f"大连西门子唛头_{formatted_date}.xlsx"
    out_file = _make_temp_output_path(".xlsx")
    try:
        wb = _build_dalian_mark_workbook(export_rows, target_date)
        wb.save(out_file)
    except HTTPException:
        if out_file.exists():
            out_file.unlink(missing_ok=True)
        raise
    except Exception as exc:
        if out_file.exists():
            out_file.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"大连唛头生成失败: {exc}") from exc

    return _file_download_response(out_file, file_name, _MEDIA_XLSX)
