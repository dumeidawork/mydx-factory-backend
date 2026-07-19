import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.api.v1.finance import extract_test_dates, get_test_fee_records, save_test_fee_records, SaveTestFeeRequest, TestFeeRecord

def test_extract_dates():
    print("Testing extract dates...")
    res = extract_test_dates()
    print(res)

def test_get_records():
    print("\nTesting get records...")
    res = get_test_fee_records("2026-01-01", "2026-12-31")
    print(res)

def test_save_records():
    print("\nTesting save records...")
    record = TestFeeRecord(
        test_date="260121",
        material_no="A5E00702891",
        specification="EN1092-1 DN150 PN16 WN RF",
        material="A105",
        quantity=68,
        remark="高径",
        order_no="4511876815",
        heat_no="5A05648",
        heat_treatment_batch_no="MY26010903",
        yield_strength="418",
        tensile_strength="607",
        elongation="37",
        reduction_of_area="73",
        hardness="192/189/185",
        impact_work="T: 212,202,142/R:216,206,140",
        product_unit_price=100.5,
        test_qty_impact=2,
        test_qty_tensile=2,
        sample_fee_impact=50,
        sample_fee_tensile=50,
        test_fee_impact=200,
        test_fee_tensile=200
    )
    req = SaveTestFeeRequest(records=[record])
    res = save_test_fee_records(req)
    print(res)

if __name__ == "__main__":
    test_extract_dates()
    test_get_records()
    test_save_records()
