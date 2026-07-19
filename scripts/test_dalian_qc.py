"""大连质检模块冒烟测试。"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.api.v1 import workflow as wf
from app.services.template_renderer import render_docx_template

from qc_test_common import extract_docx_text

TPL = wf._resolve_qc_template("dalian", "word", "A105")
OUT = BACKEND / "storage" / "test_dalian_cert.docx"


def main() -> None:
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
            "max": {
                "heat_no": "42402125",
                "part_no": "P2",
                "spec": "493",
                "yield_strength": "320",
                "tensile_strength": "500",
                "elongation": "34",
                "reduction_area": "70",
                "hardness": "145",
                "impact_test": "T:99",
            },
        },
        "chemical_analysis": [{"raw": {"C": 0.18}, "self": {"C": 0.18}}],
    }
    mapping = wf._build_qc_render_mapping(record, "dalian", "A105")
    render_docx_template(TPL, OUT, mapping)
    assert OUT.exists() and OUT.stat().st_size > 1000
    doc_text = extract_docx_text(OUT)
    assert "318" in doc_text
    assert "320" in doc_text
    expected = "4511727401_42402125_101166287.docx"
    built = f"4511727401_{wf._sanitize_file_name_part('42402125')}_{wf._sanitize_file_name_part('101166287')}.docx"
    assert built == expected
    print("dalian qc smoke test passed:", OUT.name)


if __name__ == "__main__":
    main()
