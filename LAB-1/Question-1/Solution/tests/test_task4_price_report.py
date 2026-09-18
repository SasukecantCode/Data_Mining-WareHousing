"""Task 4: price-as-of-reporting-period report, checked against the live
curated data (requires scripts/01-03 + dims.build_price_revisions_dim already run)."""
from pathlib import Path

import pytest

QUERY_SQL = (Path(__file__).resolve().parent.parent / "sql" / "queries" / "14_price_report.sql").read_text()


@pytest.mark.integration
def test_march_price_differs_from_latest_month_for_a_real_product(duck_con):
    march = duck_con.execute(QUERY_SQL, {"start_date": "2024-03-01", "end_date": "2024-03-31"}).fetchdf()
    december = duck_con.execute(QUERY_SQL, {"start_date": "2024-12-01", "end_date": "2024-12-31"}).fetchdf()
    m = march.loc[march.product_sk == 1001, "applicable_price"].iloc[0]
    d = december.loc[december.product_sk == 1001, "applicable_price"].iloc[0]
    assert float(m) == 103.45
    assert float(d) == 114.37
    assert m != d


@pytest.mark.integration
def test_resolved_price_always_within_revision_validity_window(duck_con):
    df = duck_con.execute(QUERY_SQL, {"start_date": "2024-03-01", "end_date": "2024-03-31"}).fetchdf()
    resolved = df[df["applicable_price"].notna()]
    assert ((resolved["effective_from"] <= "2024-03-31") & (resolved["effective_to"] >= "2024-03-31")).all()


@pytest.mark.integration
def test_missing_price_is_surfaced_not_substituted(duck_con):
    """product_sk=2217 (the reissued 'Local Mandi Apple 1kg', valid_from
    2024-06-01) must have NO applicable price for a March 2024 report --
    it didn't exist yet -- and a real price for a December report."""
    march = duck_con.execute(QUERY_SQL, {"start_date": "2024-03-01", "end_date": "2024-03-31"}).fetchdf()
    december = duck_con.execute(QUERY_SQL, {"start_date": "2024-12-01", "end_date": "2024-12-31"}).fetchdf()
    row_m = march[march.product_sk == 2217].iloc[0]
    row_d = december[december.product_sk == 2217].iloc[0]
    assert bool(row_m["price_missing_for_period"])
    assert row_m["applicable_price"] is None or str(row_m["applicable_price"]) == "nan"
    assert not bool(row_d["price_missing_for_period"])
    assert row_d["applicable_price"] is not None


@pytest.mark.integration
def test_mid_period_price_change_is_flagged(duck_con):
    """product_sk=1015 has a price_revisions row starting 2024-03-13 --
    a price change mid-March -- so the March report must flag it rather
    than silently picking one side."""
    march = duck_con.execute(QUERY_SQL, {"start_date": "2024-03-01", "end_date": "2024-03-31"}).fetchdf()
    row = march[march.product_sk == 1015].iloc[0]
    assert bool(row["price_changed_during_period"])


@pytest.mark.integration
def test_query_never_orders_by_effective_from_desc():
    """Structural check: the query resolves by date-range containment, not
    by picking the most recent revision regardless of the reporting date."""
    executable = "\n".join(
        line for line in QUERY_SQL.splitlines() if not line.strip().startswith("--")
    )
    assert "DESC" not in executable.upper()
    assert "LIMIT 1" not in executable.upper()
    assert "$end_date" in executable and "$start_date" in executable
    assert "effective_from <= $end_date AND" in executable and "effective_to >= $end_date" in executable
