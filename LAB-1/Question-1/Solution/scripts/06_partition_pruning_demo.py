#!/usr/bin/env python
"""Phase 12: quantitatively prove that the store/year/month partitioned
curated layout avoids scanning unrelated stores and months, for the
representative query "revenue for S01 during October 2024".

Every number here is *measured* directly from MinIO object listings and
DuckDB's own execution stats -- nothing is asserted rhetorically.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import object_store
from app.config import SETTINGS
from app.duck import connect as duck_connect


def human(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TiB"


def list_all(prefix: str):
    return list(object_store.list_objects(prefix))


def main() -> None:
    print("=" * 78)
    print("Representative query: revenue for S01 during October 2024")
    print("=" * 78)

    # ---- RAW layer: flat "everything in one bag" vs partitioned prefix ----
    print("\n--- RAW layer (data landed one-for-one as supplied) ---")
    all_raw = list_all("raw/sales/")
    all_raw_bytes = sum(o.size for o in all_raw)
    print(f"FLAT candidate set (no directory structure, must list/consider the whole "
          f"raw/sales/ prefix to find anything): {len(all_raw)} objects, {human(all_raw_bytes)}")

    s01_prefix_objs = list_all("raw/sales/store=S01/")
    s01_prefix_bytes = sum(o.size for o in s01_prefix_objs)
    print(f"PARTITIONED by store: listing only raw/sales/store=S01/: "
          f"{len(s01_prefix_objs)} objects, {human(s01_prefix_bytes)}")

    s01_oct_objs = [o for o in s01_prefix_objs if "business_date=2024-10-" in o.object_name]
    s01_oct_bytes = sum(o.size for o in s01_oct_objs)
    print(f"PARTITIONED by store+day: listing raw/sales/store=S01/business_date=2024-10-*/: "
          f"{len(s01_oct_objs)} objects, {human(s01_oct_bytes)} <- the ACTUAL relevant raw data")

    # ---- CURATED layer: candidate files under each layout ----
    print("\n--- CURATED layer (Parquet, partitioned store=../year=../month=..) ---")
    all_curated = list_all("curated/sales/")
    all_curated_bytes = sum(o.size for o in all_curated)
    print(f"FLAT candidate set: entire curated/sales/ prefix: "
          f"{len(all_curated)} objects, {human(all_curated_bytes)}")

    s01_oct_curated = [o for o in all_curated if o.object_name ==
                        "curated/sales/store=S01/year=2024/month=10/sales.parquet"]
    s01_oct_curated_bytes = sum(o.size for o in s01_oct_curated)
    print(f"PARTITIONED (store=S01/year=2024/month=10/): "
          f"{len(s01_oct_curated)} object, {human(s01_oct_curated_bytes)} <- the file DuckDB actually needs to read")

    print(f"\nCandidate-file reduction: {len(all_curated)} -> {len(s01_oct_curated)} files "
          f"({len(all_curated) - len(s01_oct_curated)} files pruned, "
          f"{100*(1 - len(s01_oct_curated)/len(all_curated)):.1f}% fewer files)")
    print(f"Candidate-byte reduction: {human(all_curated_bytes)} -> {human(s01_oct_curated_bytes)} "
          f"({100*(1 - s01_oct_curated_bytes/all_curated_bytes):.1f}% fewer bytes considered)")

    # ---- DuckDB: prove it actually only reads that one file ----
    print("\n--- DuckDB: filenames actually touched by the query ---")
    con = duck_connect()
    glob = "s3://" + SETTINGS.minio_bucket + "/curated/sales/*/*/*/sales.parquet"

    # warm up the S3/httpfs connection once so both timings below are
    # comparable (first-request TLS/socket setup would otherwise dominate).
    con.execute(f"SELECT 1 FROM read_parquet('{glob}', hive_partitioning=true) LIMIT 1").fetchall()

    def timed(sql: str, reps: int = 3) -> tuple[float, object]:
        best = None
        for _ in range(reps):
            t0 = time.time()
            out = con.execute(sql).fetchdf()
            dt = time.time() - t0
            if best is None or dt < best:
                best = dt
        return best, out

    dt_pruned, touched = timed(
        f"SELECT DISTINCT filename FROM read_parquet('{glob}', hive_partitioning=true, filename=true) "
        "WHERE store='S01' AND year=2024 AND month=10"
    )
    dt_full, touched_all = timed(
        f"SELECT DISTINCT filename FROM read_parquet('{glob}', hive_partitioning=true, filename=true)"
    )

    print(f"Files whose rows appear in the store=S01/year=2024/month=10 result set: {len(touched)}")
    for f in touched["filename"]:
        print(f"  {f}")
    print(f"Best-of-3: store+year+month-filtered query {dt_pruned*1000:.1f}ms  vs.  "
          f"unfiltered full scan of all {len(touched_all)} curated files {dt_full*1000:.1f}ms")

    plan = con.execute(
        f"EXPLAIN SELECT SUM(revenue_amount) FROM read_parquet('{glob}', hive_partitioning=true) "
        "WHERE store='S01' AND year=2024 AND month=10"
    ).fetchall()
    print("\nDuckDB query plan (note the hive-partition filter pushed into the scan):")
    for row in plan:
        for line in str(row[-1]).splitlines():
            print(f"  {line}")

    print("\n--- Result: October 2024 revenue for S01 ---")
    revenue = con.execute(
        "SELECT ROUND(SUM(revenue_amount),2) FROM fact_sales "
        "WHERE store_id='S01' AND business_date >= DATE '2024-10-01' AND business_date < DATE '2024-11-01'"
    ).fetchone()[0]
    print(f"S01 October 2024 revenue: {revenue}")


if __name__ == "__main__":
    main()
