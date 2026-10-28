from __future__ import annotations

import io
import unittest

from openpyxl import Workbook

from app.services.dalian_econtract_parser import parse_dalian_econtract_workbook


HEADERS = ["料号", "物料号", "DN大小", "订单号", "单价", "数量", "备注"]


def _xlsx_bytes(headers: list[str], rows: list[list[object]], sheet_name="Tabelle1") -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


SAMPLE_ROWS = [
    ["103018471", "FDK-123-006", 50, "4512195126", 101.76, 4, "争取能否7月1"],
    ["103018472", "FDK-123-007", 80, "4512194754", 88.5, 2, ""],
    ["103018473", "FDK-123-008", 25, "4512194752", 12, 10, "加急"],
]


class DalianEcontractParserTest(unittest.TestCase):
    def test_standard_headers(self):
        parsed = parse_dalian_econtract_workbook(_xlsx_bytes(HEADERS, SAMPLE_ROWS))
        self.assertEqual(len(parsed["items"]), 3)
        self.assertEqual(parsed["order_nos"], ["4512194752", "4512194754", "4512195126"])
        first = parsed["items"][0]
        self.assertEqual(first["part_no"], "103018471")
        self.assertEqual(first["material_no"], "FDK-123-006")
        self.assertEqual(first["dn"], "50")
        self.assertEqual(first["order_no"], "4512195126")
        self.assertEqual(first["excel_unit_price"], 101.76)
        self.assertEqual(first["quantity"], 4)
        self.assertEqual(first["remark1"], "争取能否7月1")

    def test_shuffled_headers_and_blank_column(self):
        headers = ["备注", "数量", "", "订单号", "单价", "料号", "DN大小", "物料号"]
        rows = [
            ["争取能否7月1", 4, "", "4512195126", 101.76, "103018471", 50, "FDK-123-006"],
        ]
        parsed = parse_dalian_econtract_workbook(_xlsx_bytes(headers, rows, sheet_name="任意表"))
        self.assertEqual(len(parsed["items"]), 1)
        row = parsed["items"][0]
        self.assertEqual(row["part_no"], "103018471")
        self.assertEqual(row["material_no"], "FDK-123-006")
        self.assertEqual(row["dn"], "50")
        self.assertEqual(row["order_no"], "4512195126")
        self.assertEqual(row["excel_unit_price"], 101.76)
        self.assertEqual(row["quantity"], 4)

    def test_missing_headers_raises(self):
        with self.assertRaises(ValueError):
            parse_dalian_econtract_workbook(_xlsx_bytes(["料号", "数量"], [["1", 1]]))


if __name__ == "__main__":
    unittest.main()
