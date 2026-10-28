import unittest

from app.services.qr_label_render import (
    build_datamatrix_payload,
    fill_placeholders,
    label_page_count,
    render_label_pdf,
    resolve_datamatrix_payload,
    scale_matrix_samples,
)


class QrLabelRenderTest(unittest.TestCase):
    def test_page_count_rounds_up(self):
        settings = {"cols": 4, "rows": 5}
        self.assertEqual(label_page_count(25, settings), 2)
        self.assertEqual(label_page_count(20, settings), 1)
        self.assertEqual(label_page_count(0, settings), 0)

    def test_placeholders(self):
        self.assertEqual(fill_placeholders("炉号{{heat_no}}", {"heat_no": "45678"}), "炉号45678")

    def test_payload(self):
        payload = build_datamatrix_payload(
            {
                "material_no": "FDK-083N5025",
                "heat_no": "45678",
                "heat_treatment_batch_no": "MY2511",
                "order_no": "2000581321",
                "supplier_no": "108005913",
            }
        )
        self.assertEqual(payload, "1PFDK-083N5025+1T45678_MY2511+21V108005913+K2000581321")

    def test_payload_from_template(self):
        filled = resolve_datamatrix_payload(
            {"material_no": "FDK-1", "order_no": "20001"},
            "1P{{material_no}}+K{{order_no}}",
        )
        self.assertEqual(filled, "1PFDK-1+K20001")

    def test_template_payload_is_not_empty_and_encodes_32(self):
        from app.utils.datamatrix_ecc200 import encode_datamatrix

        payload = resolve_datamatrix_payload(
            {
                "material_no": "FDK-083N5025",
                "heat_no": "45678",
                "heat_treatment_batch_no": "MY2511",
                "order_no": "2000581321",
            },
            "1P{{material_no}}+1T{{heat_no}}-{{heat_treatment_batch_no}}+21V108005913+K{{order_no}}",
        )
        self.assertTrue(payload)
        self.assertNotIn("{{", payload)
        self.assertIn("FDK-083N5025", payload)
        matrix = encode_datamatrix(payload, min_symbol=32)
        self.assertEqual(len(matrix), 32)

    def test_french_template_keeps_hyphen_between_heat_fields(self):
        payload = resolve_datamatrix_payload(
            {
                "material_no": "FDK-083N5025",
                "heat_no": "45678",
                "heat_treatment_batch_no": "MY2511",
                "order_no": "2000581321",
            },
            "1P{{material_no}}+1T{{heat_no}}-{{heat_treatment_batch_no}}+21V108005913+K{{order_no}}",
        )
        self.assertEqual(payload, "1PFDK-083N5025+1T45678-MY2511+21V108005913+K2000581321")

    def test_scale_matrix_samples_nearest_neighbor(self):
        matrix = [[1, 0], [0, 1]]
        width, height, samples = scale_matrix_samples(matrix, 16)
        self.assertEqual((width, height), (32, 32))
        self.assertEqual(samples[:16], b"\x00" * 16)
        self.assertEqual(samples[16:32], b"\xff" * 16)
        self.assertEqual(samples[-32:-16], b"\xff" * 16)
        self.assertEqual(samples[-16:], b"\x00" * 16)

    def test_render_pdf_datamatrix_is_upscaled(self):
        design = {
            "width_mm": 70,
            "height_mm": 32,
            "modules": [
                {
                    "type": "datamatrix",
                    "x_mm": 48,
                    "y_mm": 6,
                    "size_mm": 20,
                    "quiet_mm": 2.5,
                    "content": "1P{{material_no}}",
                },
            ],
            "print": {
                "paper": "A4",
                "orientation": "landscape",
                "cols": 1,
                "rows": 1,
                "gap_x_mm": 0,
                "gap_y_mm": 0,
                "margin_mm": 5,
            },
        }
        pdf = render_label_pdf(design, [{"material_no": "FDK-1", "quantity": 1}])
        import fitz

        doc = fitz.open(stream=pdf, filetype="pdf")
        try:
            images = doc[0].get_images()
            self.assertTrue(images)
            pix = fitz.Pixmap(doc, images[0][0])
            try:
                self.assertGreaterEqual(pix.width, 32 * 16)
                self.assertGreaterEqual(pix.height, 32 * 16)
            finally:
                pix = None
        finally:
            doc.close()

    def test_render_pdf_old_qr_symbology(self):
        design = {
            "width_mm": 70,
            "height_mm": 32,
            "modules": [
                {
                    "type": "datamatrix",
                    "x_mm": 48,
                    "y_mm": 6,
                    "size_mm": 20,
                    "quiet_mm": 2.5,
                    "content": "1P{{material_no}}",
                    "symbology": "qr",
                },
            ],
            "print": {
                "paper": "A4",
                "orientation": "landscape",
                "cols": 1,
                "rows": 1,
                "gap_x_mm": 0,
                "gap_y_mm": 0,
                "margin_mm": 5,
            },
        }
        pdf = render_label_pdf(design, [{"material_no": "FDK-1", "quantity": 1}])
        import fitz

        doc = fitz.open(stream=pdf, filetype="pdf")
        try:
            images = doc[0].get_images()
            self.assertTrue(images)
            pix = fitz.Pixmap(doc, images[0][0])
            try:
                self.assertGreaterEqual(pix.width, 80)
                self.assertGreaterEqual(pix.height, 80)
            finally:
                pix = None
        finally:
            doc.close()

    def test_render_pdf_fills_text_placeholders(self):
        design = {
            "width_mm": 70,
            "height_mm": 32,
            "modules": [
                {
                    "type": "text",
                    "x_mm": 2,
                    "y_mm": 2,
                    "w_mm": 40,
                    "h_mm": 6,
                    "content": "{{material_no}}",
                    "font_mm": 3.881,
                    "font_family": "Calibri",
                    "bold": True,
                },
                {
                    "type": "text",
                    "x_mm": 2,
                    "y_mm": 10,
                    "w_mm": 40,
                    "h_mm": 6,
                    "content": "Heat {{heat_no}}",
                    "font_mm": 3.881,
                    "font_family": "Calibri",
                    "bold": True,
                },
            ],
            "print": {
                "paper": "A4",
                "orientation": "landscape",
                "cols": 1,
                "rows": 1,
                "gap_x_mm": 0,
                "gap_y_mm": 0,
                "margin_mm": 5,
            },
        }
        pdf = render_label_pdf(
            design,
            [{"material_no": "FDK-083N5025", "heat_no": "45678", "quantity": 1}],
        )
        import fitz

        doc = fitz.open(stream=pdf, filetype="pdf")
        try:
            extracted = doc[0].get_text().replace(" ", "").replace("\n", "")
            self.assertIn("FDK", extracted)
            self.assertIn("083N5025", extracted)
            self.assertIn("45678", extracted)
        finally:
            doc.close()

    def test_payload_empty_template_falls_back(self):
        filled = resolve_datamatrix_payload(
            {"material_no": "FDK-1", "order_no": "20001"},
            "",
        )
        self.assertEqual(filled, "1PFDK-1+K20001")

    def test_render_pdf_label_border_follows_print_settings(self):
        base = {
            "width_mm": 70,
            "height_mm": 32,
            "modules": [],
            "print": {
                "paper": "A4",
                "orientation": "landscape",
                "cols": 1,
                "rows": 1,
                "gap_x_mm": 0,
                "gap_y_mm": 0,
                "margin_mm": 5,
            },
        }
        item = [{"material_no": "FDK-1", "quantity": 1}]
        import fitz

        def drawings_for(design):
            pdf = render_label_pdf(design, item)
            doc = fitz.open(stream=pdf, filetype="pdf")
            try:
                return doc[0].get_drawings()
            finally:
                doc.close()

        off = drawings_for(base)
        self.assertFalse(any(abs((d.get("width") or 0) - 0.75) < 1e-6 and d.get("color") == (0, 0, 0) for d in off))

        on = drawings_for({**base, "print": {**base["print"], "border_enabled": True, "border_width_px": 1}})
        self.assertTrue(any(abs((d.get("width") or 0) - 0.75) < 1e-6 and d.get("color") == (0, 0, 0) for d in on))

    def test_render_pdf_two_pages(self):
        design = {
            "width_mm": 70,
            "height_mm": 32,
            "modules": [
                {"type": "text", "x_mm": 2, "y_mm": 2, "w_mm": 40, "h_mm": 6, "content": "{{material_no}}", "font_mm": 2.5},
                {"type": "datamatrix", "x_mm": 48, "y_mm": 6, "size_mm": 20, "quiet_mm": 2.5},
            ],
            "print": {"paper": "A4", "orientation": "landscape", "cols": 4, "rows": 5, "gap_x_mm": 1, "gap_y_mm": 1, "margin_mm": 5},
        }
        pdf = render_label_pdf(design, [{"material_no": "FDK-1", "order_no": "20001", "heat_no": "H1", "heat_treatment_batch_no": "B1", "quantity": 25}])
        self.assertGreater(len(pdf), 200)
        self.assertTrue(pdf.startswith(b"%PDF"))
        import fitz

        doc = fitz.open(stream=pdf, filetype="pdf")
        try:
            self.assertEqual(doc.page_count, 2)
        finally:
            doc.close()


if __name__ == "__main__":
    unittest.main()
