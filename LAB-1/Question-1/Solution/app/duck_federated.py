"""Task 5: a federated DuckDB connection that joins MinIO Parquet and live
PostgreSQL WITHOUT copying either side.

Deliberately separate from app/duck.py (Task 1/3/4's connection), which
creates dim_store/dim_product/dim_category/dim_date VIEWS over Parquet
*snapshots* written by app/analytics/dims.py -- those snapshots are exactly
the kind of "copy" Task 5 says not to make. This module creates no such
snapshot and no views over PostgreSQL data; every query against this
connection reads `stores`/`products`/`product_categories` live, through the
`pg` catalog, for every single execution.

Only the sales fact data (already Task 1/2's curated Parquet in MinIO, the
correct home for 1.1M append-mostly analytical rows) is read via
read_parquet(); PostgreSQL is reached exclusively through the `pg` ATTACH.
"""
from __future__ import annotations

import duckdb

from app.config import SETTINGS

FACT_GLOB = f"s3://{SETTINGS.minio_bucket}/curated/sales/*/*/*/sales.parquet"


def connect_federated(debug_show_pg_queries: bool = False) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()

    # MinIO / S3 side
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")
    con.execute(f"SET s3_endpoint='{SETTINGS.minio_endpoint}'")
    con.execute(f"SET s3_access_key_id='{SETTINGS.minio_access_key}'")
    con.execute(f"SET s3_secret_access_key='{SETTINGS.minio_secret_key}'")
    con.execute(f"SET s3_use_ssl={'true' if SETTINGS.minio_secure else 'false'}")
    con.execute("SET s3_url_style='path'")

    # PostgreSQL side -- ATTACH, not COPY/CREATE TABLE AS: `pg.stores` etc.
    # remain PostgreSQL's own tables, queried live on every execution.
    con.execute("INSTALL postgres")
    con.execute("LOAD postgres")
    dsn = (
        f"host={SETTINGS.postgres_host} port={SETTINGS.postgres_port} "
        f"dbname={SETTINGS.postgres_db} user={SETTINGS.postgres_user} "
        f"password={SETTINGS.postgres_password}"
    )
    con.execute(f"ATTACH '{dsn}' AS pg (TYPE postgres, READ_ONLY)")

    if debug_show_pg_queries:
        # DuckDB's postgres extension debug setting: prints every SQL
        # statement it actually sends to PostgreSQL to stdout.
        con.execute("SET pg_debug_show_queries=true")

    return con
