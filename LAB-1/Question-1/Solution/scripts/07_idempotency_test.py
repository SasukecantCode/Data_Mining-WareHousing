#!/usr/bin/env python
"""Task 2 mandatory test: RESET ONCE, then LOAD #1 -> LOAD #2 -> LOAD #3
against the real supplied dataset, with NO reset between runs. After each
run, record row_count / logical dataset checksum / revenue from a fresh
DuckDB connection. Passes only if all three runs agree on all three values.

Writes:
  reports/task2/idempotency_test_output.txt   (this script's full stdout)
  reports/task2/idempotency_results.csv
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analytics.checksum import dataset_stats
from app.config import SETTINGS
from app.db import connect
from app.duck import connect as duck_connect
from app.loader import load, reset_destination

HERE = Path(__file__).resolve().parent.parent
REPORT_DIR = HERE / "reports" / "task2"


class Tee:
    """Writes to both stdout and a log file, unbuffered enough to survive a crash."""
    def __init__(self, path: Path):
        self.file = open(path, "w")

    def write(self, s: str) -> None:
        sys.__stdout__.write(s)
        self.file.write(s)
        self.file.flush()

    def flush(self) -> None:
        sys.__stdout__.flush()
        self.file.flush()


def run_once(run_number: int, conn) -> dict:
    print(f"\n=== LOAD #{run_number} ===")
    t0 = time.time()
    stats = load(SETTINGS.source_sales_dir, conn)
    dt = time.time() - t0
    print(f"raw files scanned:            {stats.scanned}")
    print(f"raw files landed (new/changed): {stats.landed}")
    print(f"raw files skipped (unchanged):   {stats.skipped_already_landed}")
    print(f"load elapsed:                  {dt:.1f}s")

    con = duck_connect()  # fresh connection each time -- no stale cache
    result = dataset_stats(con)
    print(f"\n=== RUN {run_number} ===")
    print(f"row_count: {result['row_count']}")
    print(f"checksum:  {result['checksum']}")
    print(f"revenue:   {result['revenue']:.2f}")
    return result


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    sys.stdout = Tee(REPORT_DIR / "idempotency_test_output.txt")

    print("Task 2 idempotency test")
    print(f"Dataset: {SETTINGS.source_sales_dir}")
    print("Sequence: RESET ONCE -> LOAD #1 -> LOAD #2 -> LOAD #3 (no reset between loads)")

    with connect() as conn:
        print("\n--- RESET (once) ---")
        reset_destination(conn)
        print("Cleared MinIO raw/ and curated/ prefixes; truncated ingestion_manifest, "
              "dedup_conflicts, enrichment_rejects, curation_runs. Master data (stores/"
              "products/price_revisions) left untouched.")

        results = []
        for i in (1, 2, 3):
            results.append(run_once(i, conn))

    print("\n" + "=" * 60)
    print("Comparison")
    print("=" * 60)
    print(f"{'Run':<5}{'Row count':>12}  {'Checksum':<20}{'Revenue':>16}")
    for i, r in enumerate(results, start=1):
        print(f"{i:<5}{r['row_count']:>12}  {r['checksum'][:16]+'...':<20}{r['revenue']:>16,.2f}")

    row_counts = {r["row_count"] for r in results}
    checksums = {r["checksum"] for r in results}
    revenues = {r["revenue"] for r in results}

    passed = len(row_counts) == 1 and len(checksums) == 1 and len(revenues) == 1

    print("\nrow_count_1 == row_count_2 == row_count_3:", row_counts.pop() if len(row_counts) == 1 else f"MISMATCH {row_counts}")
    print("checksum_1  == checksum_2  == checksum_3: ", "MATCH" if len(checksums) == 1 else f"MISMATCH ({len(checksums)} distinct)")
    print("revenue_1   == revenue_2   == revenue_3:  ", revenues.pop() if len(revenues) == 1 else f"MISMATCH {revenues}")

    print(f"\nIDEMPOTENCY: {'PASS' if passed else 'FAIL'}")

    with open(REPORT_DIR / "idempotency_results.csv", "w") as f:
        f.write("run,row_count,checksum,revenue\n")
        for i, r in enumerate(results, start=1):
            f.write(f"{i},{r['row_count']},{r['checksum']},{r['revenue']:.2f}\n")

    sys.stdout = sys.__stdout__
    print(f"\nFull log written to {REPORT_DIR / 'idempotency_test_output.txt'}")
    print(f"CSV written to {REPORT_DIR / 'idempotency_results.csv'}")

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
