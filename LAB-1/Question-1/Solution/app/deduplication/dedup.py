"""Union-by-(bill_no,line_no) deduplication across a store-day's original file
and any resend files.

Vendor rule (billing_notes.md): "the safe unit is the line, identified by
(bill_no, line_no)". A resend is not necessarily a complete replacement -- it
can be a partial re-export (see inspection report section 4: SALES_S01_20241112
has 113 keys that exist ONLY in the original). "Newest file wins" would
silently drop those lines. The correct rule is:

  * union all (bill_no, line_no) keys across every file for a store-day
    (original + every __Rn), keeping exactly one copy per key
  * if two files disagree about the content of the same key, do not silently
    pick one -- record a conflict for audit and still emit a deterministic
    row so the pipeline stays total
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class DedupResult:
    rows: pd.DataFrame
    conflicts: list[dict] = field(default_factory=list)
    n_input_rows: int = 0
    n_output_rows: int = 0
    n_duplicate_keys_resolved: int = 0


def dedup_store_day(frames: list[tuple[str, int, pd.DataFrame]]) -> DedupResult:
    """frames: list of (source_file_name, resend_seq, dataframe) for one (store, business_date).

    Files are processed in resend_seq order (0=original first, then __R1,
    __R2, ...) purely so that, when two copies of a key genuinely disagree,
    the later resend's content is the one kept (it is the operationally
    "latest correction") -- but the disagreement is still logged, it is never
    silently swallowed.
    """
    frames_sorted = sorted(frames, key=lambda t: t[1])
    n_input = sum(len(df) for _, _, df in frames_sorted)

    combined: dict[tuple[str, int], dict] = {}
    conflicts: list[dict] = []

    for source_file, resend_seq, df in frames_sorted:
        for rec in df.to_dict("records"):
            key = (rec["bill_no"], rec["line_no"])
            content_key = tuple(
                (k, v) for k, v in rec.items() if k not in ("bill_no", "line_no")
            )
            if key in combined:
                prior = combined[key]
                if prior["_content_key"] != content_key:
                    conflicts.append({
                        "bill_no": key[0],
                        "line_no": key[1],
                        "prior_source_file": prior["_source_file"],
                        "prior_line_type": prior.get("line_type"),
                        "new_source_file": source_file,
                        "new_line_type": rec.get("line_type"),
                    })
            rec["_source_file"] = source_file
            rec["_content_key"] = content_key
            combined[key] = rec

    out_records = []
    for rec in combined.values():
        rec = dict(rec)
        rec.pop("_content_key", None)
        source_file = rec.pop("_source_file")
        rec["source_file"] = source_file
        out_records.append(rec)

    out_df = pd.DataFrame.from_records(out_records) if out_records else pd.DataFrame(
        columns=[*next(iter(frames_sorted))[2].columns, "source_file"] if frames_sorted else []
    )
    if not out_df.empty:
        out_df = out_df.sort_values(["bill_no", "line_no"]).reset_index(drop=True)

    return DedupResult(
        rows=out_df,
        conflicts=conflicts,
        n_input_rows=n_input,
        n_output_rows=len(out_df),
        n_duplicate_keys_resolved=n_input - len(out_df),
    )
