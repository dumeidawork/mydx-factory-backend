import unittest

from app.utils.qrcode_label import encode_qr_matrix, make_qr_png


class QrcodeLabelTest(unittest.TestCase):
    def test_matrix_has_finder_corners(self):
        matrix = encode_qr_matrix("A")
        self.assertGreaterEqual(len(matrix), 21)
        self.assertEqual(len(matrix), len(matrix[0]))
        # QR 三处定位块左上为黑
        self.assertEqual(matrix[1][1], 1)

    def test_siemens_payload_png(self):
        payload = "1PFDK-083N5025+1T45678_MY2511+21V108005913+K2000581321"
        png = make_qr_png(payload)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertGreater(len(png), 200)
        matrix = encode_qr_matrix(payload)
        self.assertGreaterEqual(len(matrix), 21)


if __name__ == "__main__":
    unittest.main()
