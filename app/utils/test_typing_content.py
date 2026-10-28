"""打字内容拼接单测。运行：python -m unittest app.utils.test_typing_content"""
from __future__ import annotations

import unittest

from app.utils.typing_content import (
    build_spec_display,
    build_typing_content,
    format_qr_spec,
    normalize_material_key,
    split_spec_head,
    to_full_width_inch,
)

MATERIAL_MAP = {
    "A105": "A105+QT",
    "304": "A182 F304+AT",
    "316": "A182 F316/F316L+AT",
}

INCH_MAP = {
    "DN32": '1 1/4"',
    "DN300": '12"',
}


class SplitSpecHeadTests(unittest.TestCase):
    def test_en1092_spec_before_model(self):
        head = split_spec_head("EN1092-1 DN300 PN16 SO RF")
        self.assertEqual(head["standard_no"], "EN1092-1")
        self.assertEqual(head["spec"], "DN300")
        self.assertEqual(head["model"], "PN16")
        self.assertEqual(head["drawing_type"], "SO RF")

    def test_asme_model_before_spec(self):
        head = split_spec_head("ASME B16.5 CL150 DN600 SO RF")
        self.assertEqual(head["standard_no"], "ASME B16.5")
        self.assertEqual(head["spec"], "DN600")
        self.assertEqual(head["model"], "CL150")
        self.assertEqual(head["drawing_type"], "SO RF")

    def test_multiple_spaces_collapsed(self):
        head = split_spec_head(" ASME  B16.5  CL150  DN125  SO  RF ")
        self.assertEqual(head["standard_no"], "ASME B16.5")
        self.assertEqual(head["spec"], "DN125")
        self.assertEqual(head["model"], "CL150")

    def test_leading_material_no_dropped(self):
        head = split_spec_head("FDK-083N4875  EN1092-1   DN150 PN40 SO RF", "FDK-083N4875")
        self.assertEqual(head["standard_no"], "EN1092-1")
        self.assertEqual(head["spec"], "DN150")

    def test_leading_material_no_dropped_without_hint(self):
        head = split_spec_head("A5E00703748 EN1092-1 DN100 PN16 WNRF")
        self.assertEqual(head["standard_no"], "EN1092-1")
        self.assertEqual(head["drawing_type"], "WNRF")

    def test_as4087_model_first(self):
        head = split_spec_head("AS4087 PN16 DN400 SO RF")
        self.assertEqual(head["standard_no"], "AS4087")
        self.assertEqual(head["spec"], "DN400")
        self.assertEqual(head["model"], "PN16")

    def test_empty(self):
        head = split_spec_head("   ")
        self.assertEqual(head["standard_no"], "")
        self.assertEqual(head["spec"], "")


class SpecDisplayTests(unittest.TestCase):
    def test_non_asme_uses_spec(self):
        display, missing = build_spec_display(split_spec_head("EN1092-1 DN300 PN16 SO RF"), INCH_MAP)
        self.assertEqual(display, "DN300")
        self.assertEqual(missing, [])

    def test_asme_appends_nps(self):
        display, missing = build_spec_display(split_spec_head("ASME B16.5 CL150 DN300 SO RF"), INCH_MAP)
        self.assertEqual(display, "DN300/NPS12”")
        self.assertEqual(missing, [])

    def test_asme_missing_inch(self):
        display, missing = build_spec_display(split_spec_head("ASME B16.5 CL150 DN600 SO RF"), INCH_MAP)
        self.assertEqual(display, "DN600")
        self.assertEqual(missing, ["inch_value"])

    def test_inch_quote_converted(self):
        self.assertEqual(to_full_width_inch('1 1/4"'), "1 1/4”")


class BuildTypingContentTests(unittest.TestCase):
    def _build(self, spec_model: str, material: str, heat_no: str, batch_no: str):
        return build_typing_content(
            spec_model=spec_model,
            material=material,
            heat_no=heat_no,
            heat_treatment_batch_no=batch_no,
            inch_by_dn=INCH_MAP,
            typing_material_by_key=MATERIAL_MAP,
        )

    def test_template_row_a105(self):
        result = self._build("EN1092-1 DN300 PN16 SO RF", "A105", "5A16066", "MY26081001")
        self.assertEqual(
            result["content"],
            "EN1092-1 DN300 PN16 A105+QT 5A16066 MY26081001 铭远厂标",
        )
        self.assertEqual(result["missing"], [])

    def test_template_row_304(self):
        result = self._build("EN1092-1 DN65 PN16 SO RF", "304", "26601425", "MY26050304")
        self.assertEqual(
            result["content"],
            "EN1092-1 DN65 PN16 A182 F304+AT 26601425 MY26050304 铭远厂标",
        )

    def test_template_row_316(self):
        result = self._build("EN1092-1 DN200 PN10 SO RF", "316 ", "5A16066", "MY26052601")
        self.assertEqual(
            result["content"],
            "EN1092-1 DN200 PN10 A182 F316/F316L+AT 5A16066 MY26052601 铭远厂标",
        )

    def test_template_row_asme(self):
        result = self._build("ASME B16.5 CL150 DN300 SO RF", "A105", "D151734", "MY26051402")
        self.assertEqual(
            result["content"],
            "ASME B16.5 DN300/NPS12” CL150 A105+QT D151734 MY26051402 铭远厂标",
        )

    def test_user_example_dn32(self):
        result = self._build("ASME B16.5 CL150 DN32 SO RF", "A105", "5A16066", "MY26081001")
        self.assertEqual(
            result["content"],
            "ASME B16.5 DN32/NPS1 1/4” CL150 A105+QT 5A16066 MY26081001 铭远厂标",
        )

    def test_missing_heat_and_batch(self):
        result = self._build("EN1092-1 DN300 PN16 SO RF", "A105", "", "")
        self.assertEqual(result["content"], "EN1092-1 DN300 PN16 A105+QT 铭远厂标")
        self.assertIn("heat_no", result["missing"])
        self.assertIn("heat_treatment_batch_no", result["missing"])

    def test_unmapped_material(self):
        result = self._build("EN1092-1 DN300 PN16 SO RF", "F51", "5A16066", "MY26081001")
        self.assertIn("typing_material", result["missing"])
        self.assertEqual(result["content"], "EN1092-1 DN300 PN16 5A16066 MY26081001 铭远厂标")

    def test_material_key_normalized(self):
        self.assertEqual(normalize_material_key(" a105 "), "A105")


class FormatQrSpecTests(unittest.TestCase):
    def test_dn_pn(self):
        self.assertEqual(format_qr_spec("DN50", "PN40"), "DN50/PN40")
        self.assertEqual(format_qr_spec("DN50/PN40", ""), "DN50/PN40")

    def test_dn_cl(self):
        self.assertEqual(format_qr_spec("DN50", "CL150"), "DN50/CL150")
        self.assertEqual(format_qr_spec("DN50/CL150", ""), "DN50/CL150")

    def test_lb_nb(self):
        self.assertEqual(format_qr_spec("350NB", "150LB"), "DN350/CL150")
        self.assertEqual(format_qr_spec("150LB", "350NB"), "DN350/CL150")

    def test_pn_nb(self):
        self.assertEqual(format_qr_spec("750NB", "PN16"), "DN750/PN16")
        self.assertEqual(format_qr_spec("PN16", "750NB"), "DN750/PN16")

    def test_dn_only(self):
        self.assertEqual(format_qr_spec("DN700", ""), "DN700")
        self.assertEqual(format_qr_spec("DN700-800", ""), "DN700-800")

    def test_k_nb(self):
        self.assertEqual(format_qr_spec("40NB", "10K"), "DN40/10K")
        self.assertEqual(format_qr_spec("10K", "40NB"), "DN40/10K")

    def test_empty(self):
        self.assertEqual(format_qr_spec("", ""), "")


if __name__ == "__main__":
    unittest.main()
