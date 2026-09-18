"""Phase 9 (dimensions): small reference snapshots written once to
curated/dims/ so DuckDB can join fact_sales to human-readable attributes
without going back to PostgreSQL on every analytical query.

These are dimension *snapshots*, not the master-data system of record --
PostgreSQL (masters.sql) remains authoritative. dim_product deliberately
keeps one row per product_sk (not per product_code), preserving the
reissue history rather than collapsing it.
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

    stores = pd.read_sql(
        "SELECT store_id, store_name, city, state, region, floor_area_sqft, opened_on FROM stores", conn
    )
    _write(stores, pa.schema([
        ("store_id", pa.string()), ("store_name", pa.string()), ("city", pa.string()),
        ("state", pa.string()), ("region", pa.string()), ("floor_area_sqft", pa.int64()),
        ("opened_on", pa.date32()),
    ]), "curated/dims/dim_store.parquet", c)

    categories = pd.read_sql(
        "SELECT category_id, category_name, department, gst_rate FROM product_categories", conn
    )
    categories["gst_rate"] = categories["gst_rate"].map(lambda x: Decimal(str(x)))
    _write(categories, pa.schema([
        ("category_id", pa.string()), ("category_name", pa.string()),
        ("department", pa.string()), ("gst_rate", pa.decimal128(4, 3)),
    ]), "curated/dims/dim_category.parquet", c)

    products = pd.read_sql(
        "SELECT product_sk, product_code, product_name, category_id, brand, pack_size, uom, "
        "valid_from, valid_to, is_current FROM products", conn
    )
    _write(products, pa.schema([
        ("product_sk", pa.int64()), ("product_code", pa.string()), ("product_name", pa.string()),
        ("category_id", pa.string()), ("brand", pa.string()), ("pack_size", pa.string()),
        ("uom", pa.string()), ("valid_from", pa.date32()), ("valid_to", pa.date32()),
        ("is_current", pa.bool_()),
    ]), "curated/dims/dim_product.parquet", c)

    dates = pd.DataFrame({"business_date": pd.date_range(date_start, date_end, freq="D")})
    dates["date_sk"] = dates["business_date"].dt.strftime("%Y%m%d").astype("int64")
    dates["year"] = dates["business_date"].dt.year
    dates["month"] = dates["business_date"].dt.month
    dates["day"] = dates["business_date"].dt.day
    dates["iso_week"] = dates["business_date"].dt.isocalendar().week.astype("int64")
    dates["iso_year"] = dates["business_date"].dt.isocalendar().year.astype("int64")
    dates["day_name"] = dates["business_date"].dt.day_name()
    dates["business_date"] = dates["business_date"].dt.date
    _write(dates, pa.schema([
        ("business_date", pa.date32()), ("date_sk", pa.int64()), ("year", pa.int64()),
        ("month", pa.int64()), ("day", pa.int64()), ("iso_week", pa.int64()),
        ("iso_year", pa.int64()), ("day_name", pa.string()),
    ]), "curated/dims/dim_date.parquet", c)
