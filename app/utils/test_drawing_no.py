import unittest

from app.utils.drawing_no import (
    drawing_no_base,
    normalize_drawing_input,
    normalize_drawing_rev_for_db,
    should_normalize_drawing_input,
)


class DrawingInputNormalizeTest(unittest.TestCase):
    def test_rev_form_stays(self):
        self.assertEqual(normalize_drawing_input("A5E02970605A Rev017"), "A5E02970605A Rev017")

    def test_hyphen_form_to_rev(self):
        self.assertEqual(normalize_drawing_input("A5E02970605A-017"), "A5E02970605A Rev017")

    def test_plain_drawing_unchanged(self):
        self.assertEqual(normalize_drawing_input("A5E02970605A"), "A5E02970605A")

    def test_spec_like_not_rewritten(self):
        self.assertFalse(should_normalize_drawing_input("FDK-115-518"))
        self.assertEqual(normalize_drawing_input("FDK-115-518"), "FDK-115-518")

    def test_rev_for_db_still_converts_last_hyphen(self):
        self.assertEqual(normalize_drawing_rev_for_db("A5E02970605A-017"), "A5E02970605A Rev017")

    def test_drawing_no_base_strips_rev(self):
        self.assertEqual(drawing_no_base("A5E00702852A Rev002"), "A5E00702852A")
        self.assertEqual(drawing_no_base("A5E00702852A-002"), "A5E00702852A")
        self.assertEqual(drawing_no_base("A5E00702852A"), "A5E00702852A")

    def test_drawing_no_base_keeps_spec_like(self):
        self.assertEqual(drawing_no_base("FDK-115-518"), "FDK-115-518")


if __name__ == "__main__":
    unittest.main()
