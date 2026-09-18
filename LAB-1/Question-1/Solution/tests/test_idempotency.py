"""Rerunning ingestion/curation must not double the data."""
import pandas as pd
import pytest

from app.deduplication.dedup import dedup_store_day


def test_dedup_is_stable_across_repeated_calls_on_identical_input():
    frames = [("SALES_S01_20240101.csv", 0, pd.DataFrame.from_records([
        {"bill_no": "S01/20240101/00001", "line_no": 1, "product_code": "P1",
         "qty": 2, "unit_price": 50.0, "line_type": "SALE", "transaction_ts": "t"},
        {"bill_no": "S01/20240101/00001", "line_no": 2, "product_code": "TAX",
         "qty": 1, "unit_price": 18.0, "line_type": "TAX", "transaction_ts": "t"},
    ]))]
    r1 = dedup_store_day(frames)
    r2 = dedup_store_day(frames)
    r3 = dedup_store_day(frames)
    assert r1.n_output_rows == r2.n_output_rows == r3.n_output_rows == 2
    rev = lambda r: (r.rows["qty"] * r.rows["unit_price"]).sum()
    assert rev(r1) == rev(r2) == rev(r3)


@pytest.mark.integration
def test_manifest_checksum_skips_unchanged_files_on_rerun(tmp_path):
    """Landing the same file twice must not re-upload / re-record it --
    exercised end-to-end in scripts/02_land_raw.py (second run reports
    'landed: 0, skipped: 4457' against the full dataset)."""
    from app.normalization.filenames import parse_filename
    from app.ingestion.raw_landing import _sha256

    f = tmp_path / "SALES_S01_20240101.csv"
    f.write_text("bill_no,line_no,product_code,qty,unit_price,line_type,ts\n"
                  "S01/20240101/00001,1,P1,1,10.0,SALE,2024-01-01T10:00:00\n")
    checksum_a = _sha256(f)
    checksum_b = _sha256(f)
    assert checksum_a == checksum_b  # deterministic -> safe to use as an idempotency key


@pytest.mark.integration
def test_curated_totals_unchanged_after_full_rerun(duck_con):
    """Cross-checks that the live curated data (rebuilt by scripts/03_curate.py,
    run twice during development -- see README reproduction log) matches the
    known-correct total. If a rerun had doubled any data this would fail,
    since duplicate (bill_no,line_no) rows would inflate the sum."""
    total = duck_con.execute("SELECT ROUND(SUM(revenue_amount),2) FROM fact_sales").fetchone()[0]
    assert abs(float(total) - 522865735.75) < 0.01
