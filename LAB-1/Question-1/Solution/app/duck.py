"""DuckDB connection configured to read Parquet directly out of MinIO
(S3-compatible) -- this is the analytical engine end of:

    MinIO curated Parquet -> DuckDB -> SQL

No copy of the curated data is ever made into PostgreSQL; PostgreSQL stays
the master/reference-data store only.
"""
from __future__ import annotations

import duckdb

from app.config import SETTINGS

FACT_GLOB = f"s3://{SETTINGS.minio_bucket}/curated/sales/*/*/*/sales.parquet"
DIM_STORE = f"s3://{SETTINGS.minio_bucket}/curated/dims/dim_store.parquet"
DIM_CATEGORY = f"s3://{SETTINGS.minio_bucket}/curated/dims/dim_category.parquet"
DIM_PRODUCT = f"s3://{SETTINGS.minio_bucket}/curated/dims/dim_product.parquet"
DIM_DATE = f"s3://{SETTINGS.minio_bucket}/curated/dims/dim_date.parquet"


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")
    endpoint = SETTINGS.minio_endpoint
    con.execute(f"SET s3_endpoint='{endpoint}'")
    con.execute(f"SET s3_access_key_id='{SETTINGS.minio_access_key}'")
    con.execute(f"SET s3_secret_access_key='{SETTINGS.minio_secret_key}'")
    con.execute(f"SET s3_use_ssl={'true' if SETTINGS.minio_secure else 'false'}")
    con.execute("SET s3_url_style='path'")
    con.execute(f"""
        CREATE OR REPLACE VIEW fact_sales AS
        SELECT *
        FROM read_parquet('{FACT_GLOB}', hive_partitioning = true)
    """)
    con.execute(f"CREATE OR REPLACE VIEW dim_store AS SELECT * FROM read_parquet('{DIM_STORE}')")
    con.execute(f"CREATE OR REPLACE VIEW dim_category AS SELECT * FROM read_parquet('{DIM_CATEGORY}')")
    con.execute(f"CREATE OR REPLACE VIEW dim_product AS SELECT * FROM read_parquet('{DIM_PRODUCT}')")
    con.execute(f"CREATE OR REPLACE VIEW dim_date AS SELECT * FROM read_parquet('{DIM_DATE}')")

    # Task 3 dashboard star schema: a VIEW (not a re-materialized fact table)
    # that adds the surrogate keys (sales_line_sk, store_sk, date_sk) a BI
    # tool expects, computed cheaply at query time from the existing Task 1
    # fact_sales + tiny dim_store. Deliberately excludes category_id/category_sk
    # -- that is a product attribute, reached via product_sk -> dim_product,
    # never duplicated onto the fact row (see README "Important dashboard
    # attribution rule"). DISCOUNT/TAX/TENDER rows keep product_sk = NULL.
    #
    # sales_line_sk is a per-row HASH of the business key, not ROW_NUMBER():
    # a window function needs the whole input materialized and ordered
    # before it can produce a single row, which blocks DuckDB from pushing
    # a store/year/month filter down into the Parquet scan (verified with
    # EXPLAIN -- ROW_NUMBER() forced a 144/144 file scan). hash() is a plain
    # per-row scalar expression, so the filter still reaches read_parquet's
    # hive partition pruning underneath the view.
    con.execute(f"""
        CREATE OR REPLACE VIEW fact_sales_dashboard AS
        SELECT
            hash(f.bill_no || '/' || CAST(f.line_no AS VARCHAR)) AS sales_line_sk,
            f.bill_no,
            f.line_no,
            s.store_sk,
            f.store_id,
            f.product_sk,
            CAST(strftime(f.business_date, '%Y%m%d') AS BIGINT) AS date_sk,
            f.business_date,
            f.transaction_ts,
            f.qty,
            f.source_unit_price,
            f.historical_authoritative_price,
            f.line_type,
            f.revenue_amount,
            f.source_file,
            f.store AS partition_store,   -- hive partition columns, passed through so
            f.year  AS partition_year,    -- dashboard queries can filter on them directly
            f.month AS partition_month    -- for guaranteed file-level pruning (see README)
        FROM fact_sales f
        JOIN dim_store s ON s.store_id = f.store_id
    """)
    return con
