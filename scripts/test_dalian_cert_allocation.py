"""大连证书编号分配逻辑单元测试（不依赖数据库）。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.dalian_certificate_allocation import (
    format_certificate_no,
    normalize_item_no_key,
    parse_certificate_no,
    parse_qc_date_text,
    yymmdd_from_date_text,
)


def test_normalize_item_no_key() -> None:
    assert normalize_item_no_key(None) == 0
    assert normalize_item_no_key("") == 0
    assert normalize_item_no_key(10) == 10
    assert normalize_item_no_key("20") == 20


def test_format_and_parse_certificate_no() -> None:
    cert = format_certificate_no("251227", 3)
    assert cert == "ZYXMZ251227-3"
    parsed = parse_certificate_no(cert)
    assert parsed == ("251227", 3)
    assert parse_certificate_no("INVALID") is None


def test_yymmdd_from_date_text() -> None:
    assert yymmdd_from_date_text("2025-12-27") == "251227"
    assert yymmdd_from_date_text("2025-1-5") == "250105"
    dt = parse_qc_date_text("2025-1-5")
    assert dt.year == 2025 and dt.month == 1 and dt.day == 5


def test_daily_sequence_pattern() -> None:
    day = "251227"
    numbers = [format_certificate_no(day, i) for i in range(1, 4)]
    assert numbers == ["ZYXMZ251227-1", "ZYXMZ251227-2", "ZYXMZ251227-3"]


def main() -> None:
    test_normalize_item_no_key()
    test_format_and_parse_certificate_no()
    test_yymmdd_from_date_text()
    test_daily_sequence_pattern()
    print("dalian cert allocation unit tests passed")


if __name__ == "__main__":
    main()
