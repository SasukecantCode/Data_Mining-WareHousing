"""Phase 9 (dimensions) + Task 3 dashboard star schema.

Small reference snapshots written once to curated/dims/ so DuckDB can join
fact_sales to human-readable attributes without going back to PostgreSQL on
every analytical query.

These are dimension *snapshots*, not the master-data system of record --
PostgreSQL (masters.sql) remains authoritative. dim_product deliberately
keeps one row per product_sk (not per product_code), preserving the
reissue history rather than collapsing it.

Task 3 adds the dashboard-oriented columns (store_sk, category_sk,
calendar_date/quarter/month_number/year_month/week_of_year/day_of_month/
day_of_week) ADDITIVELY, alongside the original Task 1 columns
(address_line, iso_week, iso_year, business_date, day) -- nothing already
built by Task 1's queries (sql/queries/02, 05) is renamed or removed.
"""
from __future__ import annotations

import io
from decimal import Decimal

import pandas as pd
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq

from app import object_store


def _write(df: pd.DataFrame, schema: pa.Schema, object_name: str, c) -> None:
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    object_store.put_bytes(buf.getvalue(), object_name, c=c)


def build_dims(conn: psycopg.Connection, date_start: str, date_end: str) -> None:
    c = object_store.client()

    # ---- dim_store ----------------------------------------------------
    stores = pd.read_sql(
        "SELECT store_id, store_name, address_line, city, state, region, "
        "floor_area_sqft, opened_on FROM stores ORDER BY store_id", conn
    )
    stores["store_sk"] = range(1, len(stores) + 1)
    stores["address"] = stores["address_line"]  # Task 3 naming, additive
    stores = stores[["store_sk", "store_id", "store_name", "address", "address_line",
                      "city", "state", "region", "floor_area_sqft", "opened_on"]]
    _write(stores, pa.schema([
        ("store_sk", pa.int64()), ("store_id", pa.string()), ("store_name", pa.string()),
        ("address", pa.string()), ("address_line", pa.string()), ("city", pa.string()),
        ("state", pa.string()), ("region", pa.string()), ("floor_area_sqft", pa.int64()),
        ("opened_on", pa.date32()),
    ]), "curated/dims/dim_store.parquet", c)

    # ---- dim_category ---------------------------------------------------
    categories = pd.read_sql(
        "SELECT category_id, category_name, department, gst_rate "
        "FROM product_categories ORDER BY category_id", conn
    )
    categories["category_sk"] = range(1, len(categories) + 1)
    categories["gst_rate"] = categories["gst_rate"].map(lambda x: Decimal(str(x)))
    categories = categories[["category_sk", "category_id", "category_name", "department", "gst_rate"]]
    _write(categories, pa.schema([
        ("category_sk", pa.int64()), ("category_id", pa.string()), ("category_name", pa.string()),
        ("department", pa.string()), ("gst_rate", pa.decimal128(4, 3)),
    ]), "curated/dims/dim_category.parquet", c)

    # ---- dim_product ------------------------------------------------------
    products = pd.read_sql(
        "SELECT product_sk, product_code, product_name, category_id, brand, pack_size, uom, "
        "valid_from, valid_to, is_current FROM products", conn
    )
    products = products.merge(categories[["category_id", "category_sk"]], on="category_id", how="left")
    products = products[["product_sk", "product_code", "product_name", "category_id", "category_sk",
                          "brand", "pack_size", "uom", "valid_from", "valid_to", "is_current"]]
    _write(products, pa.schema([
        ("product_sk", pa.int64()), ("product_code", pa.string()), ("product_name", pa.string()),
        ("category_id", pa.string()), ("category_sk", pa.int64()), ("brand", pa.string()),
        ("pack_size", pa.string()), ("uom", pa.string()), ("valid_from", pa.date32()),
        ("valid_to", pa.date32()), ("is_current", pa.bool_()),
    ]), "curated/dims/dim_product.parquet", c)

    # ---- dim_date -----------------------------------------------------
    # Weekday convention (explicit, per Task 3): Monday=1 ... Sunday=7
    # (ISO 8601 numbering). pandas .dayofweek is Monday=0..Sunday=6, so +1.
    dt = pd.date_range(date_start, date_end, freq="D")
    dates = pd.DataFrame({"business_date": dt})
    iso = dt.isocalendar()
    dates["date_sk"] = dt.strftime("%Y%m%d").astype("int64")
    dates["calendar_date"] = dt.date
    dates["year"] = dt.year
    dates["quarter"] = dt.quarter
    dates["month"] = dt.month
    dates["month_number"] = dt.month
    dates["year_month"] = dt.strftime("%Y-%m")
    dates["day"] = dt.day
    dates["day_of_month"] = dt.day
    dates["iso_week"] = iso["week"].astype("int64").values
    dates["iso_year"] = iso["year"].astype("int64").values
    dates["week_of_year"] = iso["week"].astype("int64").values
    dates["day_of_week"] = (dt.dayofweek + 1).astype("int64")  # Monday=1..Sunday=7
    dates["day_name"] = dt.day_name()
    dates["business_date"] = dt.date

    _write(dates, pa.schema([
        ("business_date", pa.date32()), ("date_sk", pa.int64()), ("calendar_date", pa.date32()),
        ("year", pa.int64()), ("quarter", pa.int64()), ("month", pa.int64()),
        ("month_number", pa.int64()), ("year_month", pa.string()), ("day", pa.int64()),
        ("day_of_month", pa.int64()), ("iso_week", pa.int64()), ("iso_year", pa.int64()),
        ("week_of_year", pa.int64()), ("day_of_week", pa.int64()), ("day_name", pa.string()),
    ]), "curated/dims/dim_date.parquet", c)


def build_price_revisions_dim(conn: psycopg.Connection) -> None:
    """Task 4: a Parquet snapshot of price_revisions (PostgreSQL, unchanged
    system of record) so DuckDB can resolve as-of-reporting-period prices
    without a separate DB round trip per query. Written as its own function
    (not part of build_dims) so Tasks 1-3's dimension build is untouched."""
    c = object_store.client()
    revisions = pd.read_sql(
        "SELECT revision_id, product_sk, mrp, selling_price, effective_from, effective_to "
        "FROM price_revisions ORDER BY product_sk, effective_from", conn
    )
    revisions["mrp"] = revisions["mrp"].map(lambda x: Decimal(str(x)))
    revisions["selling_price"] = revisions["selling_price"].map(lambda x: Decimal(str(x)))
    _write(revisions, pa.schema([
        ("revision_id", pa.int64()), ("product_sk", pa.int64()),
        ("mrp", pa.decimal128(12, 2)), ("selling_price", pa.decimal128(12, 2)),
        ("effective_from", pa.date32()), ("effective_to", pa.date32()),
    ]), "curated/dims/dim_price_revision.parquet", c)
