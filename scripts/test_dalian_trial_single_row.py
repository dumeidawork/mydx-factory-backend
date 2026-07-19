"""大连模块三：单条试验记录只填第一行 — 单元与映射测试。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.api.v1 import heat_treatment as ht
from app.api.v1 import workflow as wf
from app.services.template_renderer import render_docx_template

from qc_test_common import extract_docx_text

TPL = wf._resolve_qc_template("dalian", "word", "A105")
OUT = Path(__file__).resolve().parents[1] / "storage" / "test_dalian_single_trial_cert.docx"


def _trial_row(row_id: int, spec_model: str, yield_strength: str = "300") -> dict:
    return {
        "id": row_id,
        "heat_no": "H001",
        "batch_no": "B001",
        "spec_model": spec_model,
        "material_no": f"P{row_id}",
        "mech_yield_strength": yield_strength,
        "mech_tensile_strength": "500",
        "mech_elongation": "30",
        "mech_reduction_area": "60",
        "mech_hardness_1": "140",
        "mech_hardness_2": "",
        "mech_hardness_3": "",
        "mech_impact_test": "T:90",
    }


def test_select_trial_min_max_rows() -> None:
    with patch.object(ht, "fetch_all", return_value=[]):
        rows, min_row, max_row = ht._select_trial_min_max_rows("H001", "B001")
        assert rows == []
        assert min_row is None
        assert max_row is None

    single = [_trial_row(1, "318 DN600", "318")]
    with patch.object(ht, "fetch_all", return_value=single):
        rows, min_row, max_row = ht._select_trial_min_max_rows("H001", "B001")
        assert len(rows) == 1
        assert min_row is single[0]
        assert max_row is None

    multi = [
        _trial_row(1, "350 DN700", "350"),
        _trial_row(2, "280 DN500", "280"),
        _trial_row(3, "318 DN600", "318"),
    ]
    with patch.object(ht, "fetch_all", return_value=multi):
        rows, min_row, max_row = ht._select_trial_min_max_rows("H001", "B001")
        assert len(rows) == 3
        assert min_row["spec_model"] == "280 DN500"
        assert max_row["spec_model"] == "350 DN700"


def test_mech_mapping_single_min_empty_max() -> None:
    min_group = wf._dalian_mech_group_from_trial(
        {
            "material_no": "P1",
            "spec_model": "318",
            "mech_yield_strength": "318",
            "mech_tensile_strength": "493",
            "mech_elongation": "33.5",
            "mech_reduction_area": "69",
            "mech_hardness_1": "142",
            "mech_hardness_2": "",
            "mech_hardness_3": "",
            "mech_impact_test": "T:98",
        },
        "42402125",
    )
    max_group = wf._dalian_mech_group_from_trial(None, "42402125")
    assert max_group == {}

    mapping = wf._build_dalian_mech_mapping(min_group, max_group, wf._qc_cert_text)
    assert mapping["mech_yield_1"] == "318"
    assert mapping["mech_tensile_1"] == "493"
    assert mapping["mech_yield_2"] == "/"
    assert mapping["mech_tensile_2"] == "/"
    assert mapping["mech_spec_2"] == "/"
    assert mapping["mech_part_no_2"] == "/"


def test_render_single_row_smoke() -> None:
    assert TPL.exists(), f"模板不存在: {TPL}"

    record = {
        "base_info": {
            "certificate_no": "ZYXMZ251227-9",
            "customer": "Siemens",
            "contract_no": "4511727401",
            "works_no": "4511727401",
            "date": "2025-12-27",
            "material": "ASTM A105",
            "state_of_delivery": "调质 Quenching +Tempering",
            "place_date": "ZHANGQIU/2025-12-27",
        },
        "delivery_content": [
            {
                "qty": 2,
                "drawing_no": "A5E02972437A Rev007",
                "part_no": "FDK-130-618",
                "name": "101166287",
                "specifications": "DN1800 PN10",
                "raw_material_no": "42402125",
            }
        ],
        "mechanical_tests": {
            "min": {
                "heat_no": "42402125",
                "part_no": "P1",
                "spec": "318",
                "yield_strength": "318",
                "tensile_strength": "493",
                "elongation": "33.5",
                "reduction_area": "69",
                "hardness": "142",
                "impact_test": "T:98",
            },
            "max": {},
        },
        "chemical_analysis": [{"raw": {"C": 0.18}, "self": {"C": 0.18}}],
    }
    min_g, max_g = wf._dalian_mech_groups_to_certificate_values(record["mechanical_tests"])
    assert max_g == {}
    mapping = wf._build_qc_render_mapping(record, "dalian", "A105")
    assert mapping.get("mech_yield_1") == "318"
    assert mapping.get("mech_yield_2") == "/"
    assert mapping.get("mech_spec_2") == "/"
    render_docx_template(TPL, OUT, mapping)
    assert OUT.exists() and OUT.stat().st_size > 1000
    doc_text = extract_docx_text(OUT)
    assert "318" in doc_text
    assert "/" in doc_text


def main() -> None:
    test_select_trial_min_max_rows()
    print("select_trial_min_max_rows: ok")
    test_mech_mapping_single_min_empty_max()
    print("mech_mapping_single_min_empty_max: ok")
    test_render_single_row_smoke()
    print("render_single_row_smoke: ok")
    print("all dalian single-trial tests passed")


if __name__ == "__main__":
    main()
