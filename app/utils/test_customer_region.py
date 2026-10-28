import unittest

from app.utils.customer_region import is_dalian_contract_customer


class DalianCustomerNameTest(unittest.TestCase):
    def test_fullwidth_ampersand(self):
        self.assertTrue(is_dalian_contract_customer("Siemens Sensors＆Communication Ltd."))

    def test_halfwidth_ampersand(self):
        self.assertTrue(is_dalian_contract_customer("Siemens Sensors&Communication Ltd."))

    def test_trim_and_spaces(self):
        self.assertTrue(is_dalian_contract_customer("  Siemens  Sensors&Communication  Ltd.  "))

    def test_france_customer(self):
        self.assertFalse(is_dalian_contract_customer("Siemens S.A.S."))

    def test_empty(self):
        self.assertFalse(is_dalian_contract_customer(""))
        self.assertFalse(is_dalian_contract_customer(None))


if __name__ == "__main__":
    unittest.main()
