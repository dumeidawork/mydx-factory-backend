import unittest

from app.utils.datamatrix_ecc200 import encode_ascii, encode_datamatrix


class DataMatrixEcc200Test(unittest.TestCase):
    def test_ascii_digit_pairs(self):
        self.assertEqual(encode_ascii("12"), [142])
        self.assertEqual(encode_ascii("A"), [66])

    def test_small_symbol_finder(self):
        matrix = encode_datamatrix("A")
        self.assertEqual(len(matrix), 10)
        self.assertEqual(len(matrix[0]), 10)
        self.assertTrue(all(row[0] == 1 for row in matrix))
        self.assertTrue(all(bit == 1 for bit in matrix[-1]))
        self.assertEqual(matrix[0], [1, 0, 1, 0, 1, 0, 1, 0, 1, 0])

    def test_siemens_sample_fits_32(self):
        payload = "1PFDK-083N5025+1T45678_MY2511+21V108005913+K2000581321"
        matrix = encode_datamatrix(payload, min_symbol=32)
        self.assertEqual(len(matrix), 32)
        self.assertEqual(len(matrix[0]), 32)
        self.assertTrue(all(row[0] == 1 for row in matrix))
        self.assertTrue(all(bit == 1 for bit in matrix[-1]))

    def test_matches_reference_encoder_without_min_size(self):
        from ppf.datamatrix import DataMatrix

        payload = "1PFDK-083N5025+1T45678_MY2511+21V108005913+K2000581321"
        ours = encode_datamatrix(payload)
        ref = [list(row) for row in DataMatrix(payload, codecs=["ascii"]).matrix]
        self.assertEqual(ours, ref)


if __name__ == "__main__":
    unittest.main()
