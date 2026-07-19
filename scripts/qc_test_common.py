"""质保书测试共用：全非空数据、模板占位符提取、输出校验。"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT = BACKEND_DIR.parent


def get_templates_root() -> Path:
    from app.core.paths import get_templates_dir

    return get_templates_dir()


def resolve_qc_pdf_template(region_dir: str, cert: str) -> Path:
    """解析 PDF 模板路径；正式模板未就绪时回退到 _acroform_build 预览。"""
    pdf_tpl = get_templates_root() / region_dir / "pdf" / cert / "quality_certificate.pdf"
    preview = (
        BACKEND_DIR
        / "storage"
        / "_acroform_build"
        / f"{region_dir.replace('-', '_')}_{cert}.pdf"
    )
    if not preview.is_file():
        return pdf_tpl
    try:
        from app.services.pdf_form_renderer import inspect_pdf_form_template

        word_path = get_templates_root() / region_dir / "word" / cert / "quality_certificate.docx"
        word_keys = extract_template_placeholders(word_path) if word_path.is_file() else set()
        report = inspect_pdf_form_template(pdf_tpl, word_keys or None)
        if report.get("passed_gate"):
            return pdf_tpl
    except Exception:
        return pdf_tpl
    return preview

CHEM_ELEMENTS = ("C", "Mn", "P", "S", "Si", "Cu", "Ni", "Cr", "Mo", "V", "CEQ")

CERT_CONFIGS = [
    ("10-DL-qc", "A105", "dalian", "ASTM A105", False),
    ("10-DL-qc", "A105_UTMTPT", "dalian", "ASTM A105", True),
    ("10-DL-qc", "304", "dalian", "AISI 304", False),
    ("10-DL-qc", "316", "dalian", "AISI 316", False),
    ("11-FR-qc", "A105", "france", "ASTM A105", False),
    ("11-FR-qc", "A105_UTMTPT", "france", "ASTM A105", True),
    ("11-FR-qc", "304", "france", "AISI 304", False),
    ("11-FR-qc", "316", "france", "AISI 316", False),
]


def extract_template_placeholders(docx_path: Path) -> set[str]:
    with zipfile.ZipFile(docx_path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))
    return set(re.findall(r"\{\{([^}]+)\}\}", text))


def extract_docx_text(docx_path: Path) -> str:
    with zipfile.ZipFile(docx_path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))


def _tag(prefix: str, key: str) -> str:
    return f"TST-{prefix}-{key}"


def build_full_record(
    region_dir: str,
    cert: str,
    region_api: str,
    material: str,
    *,
    seq: int = 1,
) -> dict[str, Any]:
    """构建全非空质检记录；字段值带唯一前缀便于校验。"""
    prefix = f"{region_dir.split('-')[0]}-{cert}-{seq}"
    order_no = f"4511999{100 + seq:03d}"
    material_no = f"MAT-{prefix}"
    heat_no = f"HEAT-{prefix}"
    batch_no = f"BATCH-{prefix}"

    base_info = {
        "certificate_no": _tag(prefix, "cert_no"),
        "customer": _tag(prefix, "customer"),
        "contract_no": _tag(prefix, "contract_no"),
        "works_no": _tag(prefix, "works_no"),
        "date": "2026-06-04",
        "material": material,
        "state_of_delivery": _tag(prefix, "state_of_delivery"),
        "article": _tag(prefix, "article"),
        "marking": _tag(prefix, "marking"),
        "place_date": f"ZHANG QIU/{_tag(prefix, 'date')}",
    }
    delivery = {
        "qty": str(10 + seq),
        "drawing_no": _tag(prefix, "drawing_no"),
        "part_no": _tag(prefix, "part_no"),
        "name": _tag(prefix, "name"),
        "item_no": str(seq),
        "specifications": _tag(prefix, "specifications"),
        "standard": _tag(prefix, "standard"),
        "raw_material_no": heat_no,
        "batch_no": batch_no,
    }

    chem_raw = {el: round(0.101 + i * 0.011, 3) for i, el in enumerate(CHEM_ELEMENTS)}
    chem_self = {el: round(0.201 + i * 0.012, 3) for i, el in enumerate(CHEM_ELEMENTS)}

    if region_api == "dalian":
        mechanical_tests = {
            "min": {
                "heat_no": _tag(prefix, "mech_heat_min"),
                "part_no": _tag(prefix, "mech_part_min"),
                "spec": _tag(prefix, "mech_spec_min"),
                "yield_strength": _tag(prefix, "yield_min"),
                "tensile_strength": _tag(prefix, "tensile_min"),
                "elongation": _tag(prefix, "elong_min"),
                "reduction_area": _tag(prefix, "reduct_min"),
                "hardness_1": _tag(prefix, "hard1_min"),
                "hardness_2": _tag(prefix, "hard2_min"),
                "hardness_3": _tag(prefix, "hard3_min"),
                "impact_test": _tag(prefix, "impact_min"),
            },
            "max": {
                "heat_no": _tag(prefix, "mech_heat_max"),
                "part_no": _tag(prefix, "mech_part_max"),
                "spec": _tag(prefix, "mech_spec_max"),
                "yield_strength": _tag(prefix, "yield_max"),
                "tensile_strength": _tag(prefix, "tensile_max"),
                "elongation": _tag(prefix, "elong_max"),
                "reduction_area": _tag(prefix, "reduct_max"),
                "hardness_1": _tag(prefix, "hard1_max"),
                "hardness_2": _tag(prefix, "hard2_max"),
                "hardness_3": _tag(prefix, "hard3_max"),
                "impact_test": _tag(prefix, "impact_max"),
            },
        }
    else:
        mechanical_tests = [
            {"field": "测试温度", "value": _tag(prefix, "mech_temp")},
            {"field": "屈服强度", "value": _tag(prefix, "mech_yield")},
            {"field": "抗拉强度", "value": _tag(prefix, "mech_tensile")},
            {"field": "伸长率", "value": _tag(prefix, "mech_elongation")},
            {"field": "断面收缩率", "value": _tag(prefix, "mech_reduction")},
            {"field": "硬度1", "value": _tag(prefix, "hard1")},
            {"field": "硬度2", "value": _tag(prefix, "hard2")},
            {"field": "硬度3", "value": _tag(prefix, "hard3")},
            {"field": "-20℃冲击试验", "value": _tag(prefix, "mech_impact")},
        ]

    return {
        "order_no": order_no,
        "material_no": material_no,
        "base_info": base_info,
        "delivery_content": [delivery],
        "mechanical_tests": mechanical_tests,
        "chemical_analysis": [{"raw": chem_raw, "self": chem_self}],
    }


def build_render_mapping(
    record: dict,
    region_api: str,
    cert: str,
    *,
    use_utmtpt: bool = False,
    mock_ut_mt_pt: bool = True,
) -> dict[str, str]:
    from contextlib import nullcontext
    from unittest.mock import patch

    from app.api.v1 import workflow as wf
    from app.services.qc_placeholder_mapping import build_certificate_mapping

    prefix = f"{region_api}-{cert}"
    ut_mt_pt_ctx = (
        patch(
            "app.api.v1.workflow._lookup_material_ut_mt_pt",
            return_value={
                "ut": _tag(prefix, "ut"),
                "mt": _tag(prefix, "mt"),
                "pt": _tag(prefix, "pt"),
            },
        )
        if mock_ut_mt_pt
        else nullcontext()
    )

    with ut_mt_pt_ctx:
        mapping = build_certificate_mapping(
            record,
            region=region_api,
            cert_type=cert,
            cert_text=wf._qc_cert_text,
            cert_number=wf._qc_cert_number,
            spec_display_fn=wf._spec_to_certificate_display,
        )
        delivery = (record.get("delivery_content") or [{}])[0]
        wf._qc_append_ut_mt_pt_mapping(
            mapping,
            record.get("material_no", ""),
            delivery.get("drawing_no", ""),
            cert_type=cert,
        )
        if cert == "A105_UTMTPT":
            mapping["ut"] = wf._qc_cert_text(_tag(prefix, "ut"))
            mapping["mt"] = wf._qc_cert_text(_tag(prefix, "mt"))
            mapping["pt"] = wf._qc_cert_text(_tag(prefix, "pt"))
    return mapping


def validate_rendered_docx(
    docx_path: Path,
    template_keys: set[str],
    mapping: dict[str, str],
) -> dict[str, Any]:
    text = extract_docx_text(docx_path)
    leftover = sorted(k for k in re.findall(r"\{\{([^}]+)\}\}", text))
    missing: list[str] = []
    checked: list[str] = []
    for key in sorted(template_keys):
        value = str(mapping.get(key, "")).strip()
        if not value or value == "/":
            continue
        checked.append(key)
        if value not in text:
            missing.append(f"{key}={value}")
    return {
        "leftover_placeholders": leftover,
        "missing_in_output": missing,
        "checked_keys": checked,
        "passed": not leftover and not missing,
    }


def validate_rendered_pdf(
    pdf_path: Path,
    template_keys: set[str],
    mapping: dict[str, str],
) -> dict[str, Any]:
    from app.services.pdf_form_renderer import extract_pdf_form_field_names, validate_filled_pdf

    keys = template_keys or extract_pdf_form_field_names(pdf_path)
    return validate_filled_pdf(pdf_path, keys, mapping, flattened=True)


def render_qc_pdf_direct(
    template_path: Path,
    output_path: Path,
    mapping: dict[str, str],
) -> dict[str, Any]:
    from app.services.pdf_form_renderer import fill_pdf_form

    return fill_pdf_form(template_path, output_path, mapping)


def convert_docx_to_pdf(docx_path: Path, pdf_path: Path) -> None:
    from app.services.docx_to_pdf import convert_docx_to_pdf as _convert

    _convert(docx_path, pdf_path)


def write_report_txt(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
