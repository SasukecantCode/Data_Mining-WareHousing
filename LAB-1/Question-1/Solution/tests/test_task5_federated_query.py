"""Task 5: federated MinIO Parquet + live PostgreSQL join, no copy of
either side. Requires: infra up, scripts/01-03 run (curated fact_sales in
MinIO), and PostgreSQL reachable at the configured host/port."""
from pathlib import Path

import pytest

from app.duck_federated import connect_federated

QUERY_SQL = (Path(__file__).resolve().parent.parent / "sql" / "queries" / "15_federated_query.sql").read_text()


@pytest.mark.integration
def test_federated_query_result_matches_task3_dashboard_query():
    """The federated live-Postgres-join result must equal the Task 3
    dashboard query's result (which joins the same fact data against a
    Parquet *snapshot* of the dimensions) -- proving the live join is
    correct, not just that it runs."""
    from app.duck import connect as duck_connect

    con_fed = connect_federated()
    fed = con_fed.execute(QUERY_SQL, {"start_date": "2024-10-01", "end_date": "2024-11-01"}).fetchdf()

    con_snap = duck_connect()
    snap = con_snap.execute("""
        SELECT s.store_id, s.store_name, cat.category_id, cat.category_name,
               ROUND(SUM(f.revenue_amount), 2) AS product_attributed_revenue
        FROM fact_sales_dashboard f
        JOIN dim_store s ON s.store_sk = f.store_sk
        JOIN dim_product p ON p.product_sk = f.product_sk
        JOIN dim_category cat ON cat.category_sk = p.category_sk
        WHERE f.business_date >= DATE '2024-10-01' AND f.business_date < DATE '2024-11-01'
        GROUP BY s.store_id, s.store_name, cat.category_id, cat.category_name
    """).fetchdf()

    merged = fed.merge(snap, on=["store_id", "category_id"], suffixes=("_fed", "_snap"))
    assert len(merged) == len(fed) == len(snap)
    assert (
        (merged["product_attributed_revenue_fed"] - merged["product_attributed_revenue_snap"]).abs() < 0.01
    ).all()


@pytest.mark.integration
def test_federated_connection_has_no_dim_parquet_views():
    """Structural check that this connection never materializes a copy of
    the PostgreSQL dimensions -- it must have NO dim_store/dim_product/
    dim_category views (those exist only on the separate app.duck
    connection, built from Parquet snapshots for Task 3/4)."""
    con = connect_federated()
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert "dim_store" not in tables
    assert "dim_product" not in tables
    assert "dim_category" not in tables


@pytest.mark.integration
def test_pg_tables_are_queried_live_not_materialized():
    """A row inserted directly into PostgreSQL is visible through the `pg`
    ATTACH on the very next query -- proof there is no cached/copied
    snapshot in DuckDB for this connection."""
    from app.db import connect as pg_connect

    con = connect_federated()
    before = con.execute("SELECT COUNT(*) FROM pg.stores").fetchone()[0]

    with pg_connect() as pg:
        cur = pg.cursor()
        cur.execute(
            "INSERT INTO stores (store_id, store_name, address_line, city, state, region, "
            "floor_area_sqft, opened_on) VALUES "
            "('ZZZ_TEST', 'Test Store', 'x', 'x', 'x', 'x', 100, DATE '2024-01-01')"
        )
    try:
        after = con.execute("SELECT COUNT(*) FROM pg.stores").fetchone()[0]
        assert after == before + 1
    finally:
        with pg_connect() as pg:
            pg.cursor().execute("DELETE FROM stores WHERE store_id = 'ZZZ_TEST'")
