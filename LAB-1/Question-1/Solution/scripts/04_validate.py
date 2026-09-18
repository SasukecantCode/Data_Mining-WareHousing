#!/usr/bin/env python
"""Phase 11-12: run data-quality checks and reconcile curated revenue against
finance_monthly.csv / _truth/truth.json (comparison only, never a pipeline input)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import connect
from app.duck import connect as duck_connect
from app.validation.validate import reconcile_with_truth, run_quality_checks


def main() -> None:
    con = duck_connect()
    with connect() as pg:
        results = run_quality_checks(con, pg)

    print("=== Data quality checks ===")
    all_passed = True
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        if not r.passed:
            all_passed = False
        print(f"[{status}] {r.name}: {r.detail}")

    print()
    print("=== Monthly reconciliation (POS-folder revenue vs truth vs finance) ===")
    recon = reconcile_with_truth(con)
    print(recon.to_string(index=False))

    print()
    if all_passed:
        print("All data quality checks PASSED.")
    else:
        print("SOME CHECKS FAILED -- see above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
