"""extract_spec_model_fields 单测。运行：python -m unittest app.utils.test_spec_parser"""
from __future__ import annotations

import unittest

from app.utils.spec_parser import extract_spec_model_fields, extract_spec_standard, spec_model_for_storage


class ExtractSpecModelFieldsTests(unittest.TestCase):
    def test_as4087_so_rf(self):
        fields = extract_spec_model_fields("FDK-115-518 AS4087 PN16 DN450 SO RF")
        self.assertEqual(fields["material_no"], "FDK-115-518")
        self.assertEqual(fields["standard_no"], "AS4087")
        self.assertEqual(fields["spec"], "DN450")
        self.assertEqual(fields["model"], "PN16")
        self.assertEqual(fields["drawing_type"], "SO RF")

    def test_asme_two_token_standard(self):
        fields = extract_spec_model_fields("FDK-083N5035 ASME B16.5 CL150 DN300 SO FF")
        self.assertEqual(fields["material_no"], "FDK-083N5035")
        self.assertEqual(fields["standard_no"], "ASME B16.5")
        self.assertEqual(fields["spec"], "DN300")
        self.assertEqual(fields["model"], "CL150")
        self.assertEqual(fields["drawing_type"], "SO FF")

    def test_en1092_wnrf(self):
        fields = extract_spec_model_fields("A5E00703748 EN1092-1 DN100 PN16 WNRF")
        self.assertEqual(fields["material_no"], "A5E00703748")
        self.assertEqual(fields["standard_no"], "EN1092-1")
        self.assertEqual(fields["spec"], "DN100")
        self.assertEqual(fields["model"], "PN16")
        self.assertEqual(fields["drawing_type"], "WNRF")

    def test_wn_rf_two_token_type(self):
        fields = extract_spec_model_fields("A5E45033247 ASME B16.5 CL600 DN40 WN RF")
        self.assertEqual(fields["material_no"], "A5E45033247")
        self.assertEqual(fields["standard_no"], "ASME B16.5")
        self.assertEqual(fields["drawing_type"], "WN RF")

    def test_extract_spec_standard_compat(self):
        spec, model = extract_spec_standard("FDK-115-518 AS4087 PN16 DN450 SO RF")
        self.assertEqual(spec, "DN450")
        self.assertEqual(model, "PN16")

    def test_empty(self):
        fields = extract_spec_model_fields("  ")
        self.assertEqual(fields["material_no"], "")
        self.assertEqual(fields["spec"], "")


class SpecModelForStorageTests(unittest.TestCase):
    def test_strip_material_no_with_double_space(self):
        stored = spec_model_for_storage("FDK-117-005P  ASME B16.5 CL150 DN40 SO RF")
        self.assertEqual(stored, "ASME B16.5 CL150 DN40 SO RF")

    def test_already_stripped_stays(self):
        stored = spec_model_for_storage("ASME B16.5 CL150 DN40 SO RF", "FDK-117-005P")
        self.assertEqual(stored, "ASME B16.5 CL150 DN40 SO RF")
