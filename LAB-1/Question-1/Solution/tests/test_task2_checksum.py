"""Task 2: the logical dataset checksum must be a function of the DATA, not
of file/row physical order or how many curated files it's split across."""
import duckdb
import pytest

from app.analytics.checksum import logical_dataset_checksum

_COLUMNS = """
    bill_no VARCHAR, line_no BIGINT, store_id VARCHAR, business_date DATE,
    transaction_ts TIMESTAMP, product_code VARCHAR, qty BIGINT,
    source_unit_price DECIMAL(12,2), line_type VARCHAR, product_sk BIGINT,
    category_id VARCHAR, historical_authoritative_price DECIMAL(12,2),
    revenue_amount DECIMAL(14,2)
"""

_ROWS = [
    ("S01/20240101/00001", 1, "S01", "2024-01-01", "2024-01-01 10:00:00",
     "P1", 1, 10.00, "SALE", 1001, "C01", 10.00, 10.00),
    ("S01/20240101/00001", 2, "S01", "2024-01-01", "2024-01-01 10:00:00",
     "TAX", 1, 1.80, "TAX", None, None, None, 0.00),
]


def _make_con(rows_in_order) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"CREATE TABLE fact_sales ({_COLUMNS})")
    con.executemany(
        "INSERT INTO fact_sales VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows_in_order
    )
    return con


def test_checksum_is_deterministic_for_same_data():
    con1 = _make_con(_ROWS)
    con2 = _make_con(_ROWS)
    assert logical_dataset_checksum(con1) == logical_dataset_checksum(con2)


def test_checksum_is_independent_of_physical_row_order():
    con_forward = _make_con(_ROWS)
    con_reversed = _make_con(list(reversed(_ROWS)))
    assert logical_dataset_checksum(con_forward) == logical_dataset_checksum(con_reversed)


def test_checksum_changes_if_a_line_is_missing():
    con_full = _make_con(_ROWS)
    con_partial = _make_con(_ROWS[:1])
    assert logical_dataset_checksum(con_full) != logical_dataset_checksum(con_partial)


def test_checksum_changes_if_a_value_differs():
    changed = list(_ROWS)
    changed[0] = changed[0][:6] + (2,) + changed[0][7:]  # qty 1 -> 2
    con_a = _make_con(_ROWS)
    con_b = _make_con(changed)
    assert logical_dataset_checksum(con_a) != logical_dataset_checksum(con_b)


@pytest.mark.integration
def test_task2_three_run_results_csv_shows_pass():
    """Cross-checks the actual saved evidence from scripts/07_idempotency_test.py
    (reports/task2/idempotency_results.csv) -- all three runs must agree."""
    from pathlib import Path
    import csv as csv_mod

    path = Path(__file__).resolve().parent.parent / "reports" / "task2" / "idempotency_results.csv"
    assert path.exists(), "run scripts/07_idempotency_test.py first"
    with open(path) as f:
        rows = list(csv_mod.DictReader(f))
    assert len(rows) == 3
    assert len({r["row_count"] for r in rows}) == 1
    assert len({r["checksum"] for r in rows}) == 1
    assert len({r["revenue"] for r in rows}) == 1
