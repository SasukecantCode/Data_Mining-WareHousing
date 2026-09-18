#!/usr/bin/env python
"""Task 2 resend test: prove the dedup logic on a REAL resend pair from the
supplied dataset, not just repeated execution.

SALES_S01_20241112.csv / SALES_S01_20241112__R1.csv is a genuine partial
resend (see reports/inspection_report.md section 4): the resend has FEWER
(bill_no,line_no) keys than the original, not more. "Latest file wins"
would silently drop the 113 keys that only exist in the original.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import object_store
from app.deduplication.dedup import dedup_store_day
from app.normalization.readers import read_source
import io

STORE_ID = "S01"
ORIGINAL = "SALES_S01_20241112.csv"
RESEND = "SALES_S01_20241112__R1.csv"
BUSINESS_DATE = "2024-11-12"


def main() -> None:
    c = object_store.client()
    orig_obj = f"raw/sales/store={STORE_ID}/business_date={BUSINESS_DATE}/{ORIGINAL}"
    resend_obj = f"raw/sales/store={STORE_ID}/business_date={BUSINESS_DATE}/{RESEND}"

    orig_df = read_source(io.BytesIO(object_store.get_bytes(orig_obj, c=c)), STORE_ID, "csv")
    resend_df = read_source(io.BytesIO(object_store.get_bytes(resend_obj, c=c)), STORE_ID, "csv")

    orig_keys = set(zip(orig_df["bill_no"], orig_df["line_no"]))
    resend_keys = set(zip(resend_df["bill_no"], resend_df["line_no"]))

    duplicate_keys = orig_keys & resend_keys
    new_in_resend = resend_keys - orig_keys
    only_in_original = orig_keys - resend_keys

    result = dedup_store_day([
        (ORIGINAL, 0, orig_df),
        (RESEND, 1, resend_df),
    ])

    print(f"original file:            {ORIGINAL}")
    print(f"resend file:              {RESEND}")
    print(f"original rows:            {len(orig_df)}")
    print(f"resend rows:              {len(resend_df)}")
    print(f"duplicate business lines: {len(duplicate_keys)}  (present in both, byte-identical where checked)")
    print(f"new business lines:       {len(new_in_resend)}  (present only in the resend)")
    print(f"lines only in original:   {len(only_in_original)}  (would be LOST by 'latest file wins')")
    print(f"final unique business lines: {result.n_output_rows}")
    print(f"dedup conflicts detected: {len(result.conflicts)}")

    expected_union = len(orig_keys | resend_keys)
    assert result.n_output_rows == expected_union, "union size mismatch"

    print("\nCheck: every line that existed ONLY in the original survives in the final dataset:", end=" ")
    final_keys = set(zip(result.rows["bill_no"], result.rows["line_no"]))
    print("PASS" if only_in_original.issubset(final_keys) else "FAIL")


if __name__ == "__main__":
    main()
