"""Revenue semantics (billing_notes.md section 'Line types').

| line_type | revenue?                                             |
|-----------|-------------------------------------------------------|
| SALE      | yes, qty*unit_price (qty > 0)                          |
| RETURN    | yes, qty*unit_price (qty < 0, subtracts)                |
| DISCOUNT  | yes, qty*unit_price (qty=1, unit_price < 0, subtracts)  |
| VOID      | yes, qty*unit_price (qty negated vs. the SALE it cancels) |
| TAX       | no  -> 0                                                |
| TENDER    | no  -> 0 (it is the bill total restated as a row)       |

One formula covers SALE/RETURN/DISCOUNT/VOID uniformly: revenue_amount =
qty * unit_price, because the source data already encodes the sign correctly
for each of those (confirmed against real RETURN/DISCOUNT/VOID rows in the
inspection report). Only TAX and TENDER are forced to zero.
"""
from __future__ import annotations

import pandas as pd

NON_REVENUE_LINE_TYPES = {"TAX", "TENDER"}


def compute_revenue(df: pd.DataFrame) -> pd.Series:
    price_col = "source_unit_price" if "source_unit_price" in df.columns else "unit_price"
    amount = df["qty"] * df[price_col]
    amount = amount.where(~df["line_type"].isin(NON_REVENUE_LINE_TYPES), 0.0)
    return amount.round(2)
