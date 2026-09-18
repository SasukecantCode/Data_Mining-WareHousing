"""business_date must come from the filename, never from transaction_ts."""
from datetime import date
from pathlib import Path

import pytest

from app.normalization.filenames import InvalidSourceFileName, parse_filename


def test_business_date_parsed_from_filename():
    meta = parse_filename(Path("SALES_S01_20240102.csv"))
    assert meta.store_id == "S01"
    assert meta.business_date == date(2024, 1, 2)
    assert meta.resend_seq == 0


def test_resend_suffix_parsed():
    meta = parse_filename(Path("SALES_S03_20241014__R2.csv"))
    assert meta.resend_seq == 2
    assert meta.is_resend


def test_invalid_filename_rejected():
    with pytest.raises(InvalidSourceFileName):
        parse_filename(Path("not_a_sales_file.csv"))


@pytest.mark.integration
def test_real_midnight_crossing_bill_keeps_filename_business_date(duck_con):
    """S01/20240102/00029: filename says 2024-01-02, but every ts inside is
    2024-01-03T00:49:28 (a bill punched ~49 minutes after midnight). See
    reports/inspection_report.md section 3."""
    row = duck_con.execute(
        "SELECT business_date, transaction_ts FROM fact_sales "
        "WHERE bill_no = 'S01/20240102/00029' LIMIT 1"
    ).fetchone()
    assert row is not None, "expected fixture bill not found -- did you run scripts/01-03?"
    business_date, transaction_ts = row
    assert str(business_date) == "2024-01-02"
    assert str(transaction_ts).startswith("2024-01-03")
    assert business_date != transaction_ts.date()
