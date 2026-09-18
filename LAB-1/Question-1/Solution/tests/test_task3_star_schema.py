"""Task 3: dashboard star schema invariants, checked against the live
curated data (requires scripts/01-03 already run)."""
import pytest


@pytest.mark.integration
def test_fact_row_count_matches_task2_dataset(duck_con):
    n = duck_con.execute("SELECT COUNT(*) FROM fact_sales_dashboard").fetchone()[0]
    assert n == 1_120_924


@pytest.mark.integration
def test_sales_line_sk_is_unique(duck_con):
    n, distinct_n = duck_con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT sales_line_sk) FROM fact_sales_dashboard"
    ).fetchone()
    assert n == distinct_n


@pytest.mark.integration
def test_product_sk_populated_only_for_sale_return_void(duck_con):
    rows = duck_con.execute(
        "SELECT line_type, "
        "SUM(CASE WHEN product_sk IS NOT NULL THEN 1 ELSE 0 END) has_sk, "
        "COUNT(*) total "
        "FROM fact_sales_dashboard GROUP BY line_type"
    ).fetchdf()
    for _, row in rows.iterrows():
        if row["line_type"] in ("SALE", "RETURN"):
            assert row["has_sk"] == row["total"], f"{row['line_type']} should always have product_sk"
        elif row["line_type"] in ("DISCOUNT", "TAX", "TENDER"):
            assert row["has_sk"] == 0, f"{row['line_type']} must never have product_sk"
        # VOID is mixed by design: it cancels a real product line (has product_sk)
        # UNLESS it cancels a DISCOUNT line -- see test_void_of_discount_has_no_product_sk.


@pytest.mark.integration
def test_void_of_discount_has_no_product_sk(duck_con):
    """VOID normally has product_sk populated (it cancels a SALE), except the
    edge case of a VOID that cancels a DISCOUNT line (product_code='DISC')."""
    n = duck_con.execute(
        "SELECT COUNT(*) FROM fact_sales WHERE line_type='VOID' AND product_code='DISC' AND product_sk IS NOT NULL"
    ).fetchone()[0]
    assert n == 0


@pytest.mark.integration
def test_dim_date_weekday_convention_monday_is_1(duck_con):
    row = duck_con.execute(
        "SELECT day_of_week, day_name FROM dim_date WHERE calendar_date = DATE '2024-01-01'"
    ).fetchone()
    assert row == (1, "Monday")  # 2024-01-01 is a real Monday


@pytest.mark.integration
def test_category_reached_only_through_product_not_stored_on_fact(duck_con):
    cols = {r[0] for r in duck_con.execute("DESCRIBE fact_sales_dashboard").fetchall()}
    assert "category_id" not in cols and "category_sk" not in cols


@pytest.mark.integration
def test_schema_is_snowflake_not_flattened_star(duck_con):
    """dim_product carries category_sk as a foreign key only -- category's
    descriptive attributes (name/department/gst_rate) live exclusively in
    dim_category, one join further out, not duplicated onto dim_product."""
    product_cols = {r[0] for r in duck_con.execute("DESCRIBE dim_product").fetchall()}
    assert "category_sk" in product_cols  # the FK must be present
    assert not {"category_name", "department", "gst_rate"} & product_cols

    category_cols = {r[0] for r in duck_con.execute("DESCRIBE dim_category").fetchall()}
    assert {"category_sk", "category_name", "department", "gst_rate"}.issubset(category_cols)


@pytest.mark.integration
def test_two_hop_join_to_category_returns_correct_category(duck_con):
    """fact -> dim_product -> dim_category for a real reissued product,
    proving the snowflake join chain resolves the RIGHT category for each
    side of the 2024-06-01 reissue (C04 before, C12 after)."""
    rows = duck_con.execute("""
        SELECT cat.category_id, MIN(f.business_date), MAX(f.business_date)
        FROM fact_sales_dashboard f
        JOIN dim_product p ON p.product_sk = f.product_sk
        JOIN dim_category cat ON cat.category_sk = p.category_sk
        WHERE p.product_code = 'P108206'
        GROUP BY cat.category_id ORDER BY 2
    """).fetchall()
    assert [r[0] for r in rows] == ["C04", "C12"]


@pytest.mark.integration
def test_total_vs_product_attributed_revenue_reconciles(duck_con):
    row = duck_con.execute(
        "SELECT "
        "SUM(revenue_amount) AS total, "
        "SUM(CASE WHEN product_sk IS NOT NULL THEN revenue_amount ELSE 0 END) AS product_attributed, "
        "SUM(CASE WHEN product_sk IS NULL THEN revenue_amount ELSE 0 END) AS non_product "
        "FROM fact_sales_dashboard "
        "WHERE business_date >= DATE '2024-10-01' AND business_date < DATE '2024-11-01'"
    ).fetchone()
    total, product_attributed, non_product = (float(x) for x in row)
    assert abs(total - (product_attributed + non_product)) < 0.01
