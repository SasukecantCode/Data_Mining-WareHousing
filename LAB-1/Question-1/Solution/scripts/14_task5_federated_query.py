#!/usr/bin/env python
"""Task 5: run the single federated query (sql/queries/15_federated_query.sql)
against live MinIO Parquet + live PostgreSQL, print the actual result, the
actual EXPLAIN ANALYZE plan, and (with pg_debug_show_queries enabled) the
actual SQL DuckDB sends to PostgreSQL to execute it.

NOTE: this script prints everything to real stdout deliberately -- DuckDB's
`pg_debug_show_queries` writes at the C++ level, bypassing Python's
sys.stdout object, so capture this script's output at the SHELL level
(`> file.txt 2>&1`), not by wrapping sys.stdout in Python. See
reports/task5/README or the project README for the exact command used.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.duck_federated import connect_federated

HERE = Path(__file__).resolve().parent.parent
QUERY_SQL = (HERE / "sql" / "queries" / "15_federated_query.sql").read_text()

PARAMS = {"start_date": "2024-10-01", "end_date": "2024-11-01"}


def main() -> None:
    print("=" * 78)
    print("Task 5: federated query -- MinIO Parquet (sales) + live PostgreSQL (dims)")
    print("=" * 78)
    print("\nQuery file: sql/queries/15_federated_query.sql")
    print(f"Parameters: {PARAMS}\n")

    print("--- 1. Actual query result ---")
    con = connect_federated(debug_show_pg_queries=False)
    result = con.execute(QUERY_SQL, PARAMS).fetchdf()
    print(result.to_string(index=False))
    print(f"\n{len(result)} rows, total revenue = {result['product_attributed_revenue'].sum():,.2f}")

    print("\n" + "=" * 78)
    print("--- 2. EXPLAIN ANALYZE (actual execution plan + timings) ---")
    print("=" * 78)
    con2 = connect_federated(debug_show_pg_queries=False)
    plan_rows = con2.execute(f"EXPLAIN ANALYZE {QUERY_SQL}", PARAMS).fetchall()
    for row in plan_rows:
        print(row[-1])

    print("\n" + "=" * 78)
    print("--- 3. Actual SQL DuckDB sent to PostgreSQL (pg_debug_show_queries=true) ---")
    print("=" * 78)
    con3 = connect_federated(debug_show_pg_queries=True)
    con3.execute(QUERY_SQL, PARAMS).fetchall()  # queries print to stdout as a side effect

    print("\n" + "=" * 78)
    print("--- 4. Contrast: a filter that DOES target a PostgreSQL column gets pushed down ---")
    print("=" * 78)
    print("(sql/queries/15 has no filter on store/product/category -- only on")
    print(" business_date, which lives in Parquet, not PostgreSQL. To show pushdown")
    print(" genuinely happens when it CAN apply -- and isn't just claimed -- here is")
    print(" a second, minimal query with a real PostgreSQL-side filter:)\n")
    print("SELECT product_sk, category_id FROM pg.products WHERE category_id = 'C04';\n")
    con4 = connect_federated(debug_show_pg_queries=True)
    n = len(con4.execute("SELECT product_sk, category_id FROM pg.products WHERE category_id = 'C04'").fetchall())
    print(f"\n-> {n} rows returned; note the WHERE clause appears verbatim in the COPY")
    print("   statement DuckDB sent to PostgreSQL above -- real pushdown, not assumed.")


if __name__ == "__main__":
    main()
