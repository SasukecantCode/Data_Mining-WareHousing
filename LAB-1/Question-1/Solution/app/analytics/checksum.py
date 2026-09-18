"""Task 2: a deterministic checksum of the *logical* curated dataset.

Not a checksum of a Parquet file's bytes (partition file count, row-group
layout, compression settings, or write order can all change the physical
bytes without the data meaning anything different). Instead: pick the
canonical business columns, force a stable string representation, sort by
the business key (bill_no, line_no) -- which is globally unique since
bill_no already encodes store_id and business_date -- concatenate, and
sha256 the result. Two loader runs that produced the same set of business
lines produce the same checksum, regardless of how many curated files or in
what order rows were written.
"""
from __future__ import annotations

import hashlib

import duckdb

_ROW_SQL = """
SELECT concat_ws('|',
    bill_no,
    CAST(line_no AS VARCHAR),
    store_id,
    CAST(business_date AS VARCHAR),
    CAST(transaction_ts AS VARCHAR),
    product_code,
    CAST(qty AS VARCHAR),
    CAST(source_unit_price AS VARCHAR),
    line_type,
    COALESCE(CAST(product_sk AS VARCHAR), 'NULL'),
    COALESCE(category_id, 'NULL'),
    COALESCE(CAST(historical_authoritative_price AS VARCHAR), 'NULL'),
    CAST(revenue_amount AS VARCHAR)
) AS row_str
FROM fact_sales
ORDER BY bill_no, line_no
"""


def logical_dataset_checksum(con: duckdb.DuckDBPyConnection) -> str:
    h = hashlib.sha256()
    reader = con.execute(_ROW_SQL).to_arrow_reader(batch_size=50_000)
    for batch in reader:
        for row_str in batch.column("row_str"):
            h.update(row_str.as_py().encode("utf-8"))
            h.update(b"\n")
    return h.hexdigest()


def dataset_stats(con: duckdb.DuckDBPyConnection) -> dict:
    row_count, revenue = con.execute(
        "SELECT COUNT(*), ROUND(SUM(revenue_amount), 2) FROM fact_sales"
    ).fetchone()
    checksum = logical_dataset_checksum(con)
    return {"row_count": row_count, "checksum": checksum, "revenue": float(revenue)}
