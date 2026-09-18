"""product_code is not a stable product identity across time (masters.sql:
P108206 was retired 2024-05-31 and reissued to a different product
2024-06-01). Resolution must use product_code + business_date, and the
same product_code must resolve to two different product_sk values on either
side of the reissue date."""
import pytest


@pytest.mark.integration
def test_reissued_code_resolves_to_two_different_products(duck_con):
    rows = duck_con.execute(
        "SELECT DISTINCT product_sk, category_id FROM fact_sales WHERE product_code = 'P108206'"
    ).fetchdf()
    assert len(rows) == 2
    assert set(rows["category_id"]) == {"C04", "C12"}


@pytest.mark.integration
def test_reissue_split_falls_on_the_documented_date(duck_con):
    before = duck_con.execute(
        "SELECT MAX(business_date) FROM fact_sales WHERE product_code='P108206' AND category_id='C04'"
    ).fetchone()[0]
    after = duck_con.execute(
        "SELECT MIN(business_date) FROM fact_sales WHERE product_code='P108206' AND category_id='C12'"
    ).fetchone()[0]
    assert str(before) == "2024-05-31"
    assert str(after) == "2024-06-01"


@pytest.mark.integration
def test_joining_by_product_code_alone_would_double_count(duck_con):
    """Sanity check that the reissue is a real fan-out trap: a naive join on
    product_code alone (ignoring validity dates) would match 2 master rows
    for every P108206 sale line."""
    naive_join_multiplier = duck_con.execute(
        "SELECT COUNT(*) FROM dim_product WHERE product_code = 'P108206'"
    ).fetchone()[0]
    assert naive_join_multiplier == 2
