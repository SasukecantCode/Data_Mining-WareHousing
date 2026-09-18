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
    return con
