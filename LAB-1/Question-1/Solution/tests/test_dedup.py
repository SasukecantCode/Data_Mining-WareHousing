"""Unit tests for (bill_no,line_no) dedup: no-resend-wins-by-default,
partial resends preserved, exact rerun idempotency, conflict detection."""
import pandas as pd

from app.deduplication.dedup import dedup_store_day


def _rows(records):
    return pd.DataFrame.from_records(records)


def test_byte_identical_resend_collapses_to_one_copy():
    original = _rows([
        {"bill_no": "S01/20240101/00001", "line_no": 1, "product_code": "P1",
         "qty": 1, "unit_price": 10.0, "line_type": "SALE", "transaction_ts": "2024-01-01T10:00:00"},
    ])
    resend = original.copy()  # byte-identical resend
    result = dedup_store_day([("orig.csv", 0, original), ("orig__R1.csv", 1, resend)])
    assert result.n_output_rows == 1
    assert result.n_duplicate_keys_resolved == 1
    assert len(result.conflicts) == 0


def test_partial_resend_does_not_drop_lines_only_in_original():
    """Mirrors the real SALES_S01_20241112 case: the resend has FEWER keys,
    not a superset -- 'newest wins' would silently drop the missing ones."""
    original = _rows([
        {"bill_no": "B1", "line_no": 1, "product_code": "P1", "qty": 1,
         "unit_price": 10.0, "line_type": "SALE", "transaction_ts": "t"},
        {"bill_no": "B1", "line_no": 2, "product_code": "P2", "qty": 1,
         "unit_price": 20.0, "line_type": "SALE", "transaction_ts": "t"},
    ])
    partial_resend = original.iloc[[0]].copy()  # only line 1 re-sent
    result = dedup_store_day([("orig.csv", 0, original), ("orig__R1.csv", 1, partial_resend)])
    assert result.n_output_rows == 2
    assert set(zip(result.rows["bill_no"], result.rows["line_no"])) == {("B1", 1), ("B1", 2)}


def test_rerunning_dedup_on_the_same_input_does_not_double_revenue():
    original = _rows([
        {"bill_no": "B1", "line_no": 1, "product_code": "P1", "qty": 3,
         "unit_price": 5.0, "line_type": "SALE", "transaction_ts": "t"},
    ])
    first = dedup_store_day([("orig.csv", 0, original)])
    second = dedup_store_day([("orig.csv", 0, original)])
    assert first.n_output_rows == second.n_output_rows == 1
    assert (first.rows["qty"] * first.rows["unit_price"]).sum() == \
           (second.rows["qty"] * second.rows["unit_price"]).sum()


def test_conflicting_content_for_same_key_is_flagged_not_hidden():
    original = _rows([
        {"bill_no": "B1", "line_no": 1, "product_code": "P1", "qty": 1,
         "unit_price": 10.0, "line_type": "SALE", "transaction_ts": "t"},
    ])
    conflicting_resend = _rows([
        {"bill_no": "B1", "line_no": 1, "product_code": "P1", "qty": 2,  # different qty!
         "unit_price": 10.0, "line_type": "SALE", "transaction_ts": "t"},
    ])
    result = dedup_store_day([("orig.csv", 0, original), ("orig__R1.csv", 1, conflicting_resend)])
    assert len(result.conflicts) == 1
    assert result.n_output_rows == 1  # still total: one deterministic row is kept
