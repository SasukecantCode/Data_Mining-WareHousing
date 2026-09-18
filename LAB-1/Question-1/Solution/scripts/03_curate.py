#!/usr/bin/env python
"""Phases 5-9: normalize, deduplicate, enrich, compute revenue, and write the
partitioned curated Parquet layer + dimension snapshots to MinIO."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analytics.curate import curate_all
from app.analytics.dims import build_dims
from app.db import connect


def main() -> None:
    t0 = time.time()
    with connect() as conn:
        results = curate_all(conn)
        build_dims(conn, "2024-01-01", "2024-12-31")
    dt = time.time() - t0

    total_input = sum(r.input_row_count for r in results)
    total_output = sum(r.output_row_count for r in results)
    total_revenue = sum(r.revenue_amount for r in results)
    total_conflicts = sum(len(r.conflicts) for r in results)
    total_rejects = sum(len(r.rejects) for r in results)

    print(f"Store-months curated:      {len(results)}")
    print(f"Raw input rows (all files):{total_input}")
    print(f"Curated unique rows:       {total_output}")
    print(f"Duplicate lines removed:   {total_input - total_output}")
    print(f"Dedup conflicts flagged:   {total_conflicts}")
    print(f"Enrichment rejects:        {total_rejects}")
    print(f"Total revenue (all time):  {total_revenue:,.2f}")
    print(f"Elapsed: {dt:.1f}s")


if __name__ == "__main__":
    main()
