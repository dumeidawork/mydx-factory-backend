import unittest

from app.api.v1.drawing_flange_type_maps import lookup_flange_type, normalize_flange_type
from app.utils.drawing_no import drawing_no_base


class FlangeTypeNormalizeTest(unittest.TestCase):
    def test_chinese_passthrough(self):
        self.assertEqual(normalize_flange_type("平板"), "平板")
        self.assertEqual(normalize_flange_type("滑动"), "滑动")
        self.assertEqual(normalize_flange_type("盲板"), "盲板")
        self.assertEqual(normalize_flange_type("高径"), "高径")

    def test_excel_abbreviations(self):
        self.assertEqual(normalize_flange_type("SO"), "滑动")
        self.assertEqual(normalize_flange_type("PL"), "平板")
        self.assertEqual(normalize_flange_type("WN"), "高径")
        self.assertEqual(normalize_flange_type("BL"), "盲板")
        self.assertEqual(normalize_flange_type("WN/2"), "高径")
        self.assertEqual(normalize_flange_type("SOP/2"), "滑动")
        self.assertEqual(normalize_flange_type("SOH/2/PTFE"), "滑动")

    def test_lookup_uses_base_drawing_no(self):
        mapping = {"A5E00702852A": "平板"}
        self.assertEqual(lookup_flange_type("A5E00702852A Rev002", mapping), "平板")
        self.assertEqual(lookup_flange_type("A5E00702852A-002", mapping), "平板")
        self.assertEqual(lookup_flange_type("A5E00702852A", mapping), "平板")
        self.assertEqual(lookup_flange_type("A5E99999999A Rev001", mapping), "")
        self.assertEqual(drawing_no_base("A5E00702852A Rev002"), "A5E00702852A")


if __name__ == "__main__":
    unittest.main()
