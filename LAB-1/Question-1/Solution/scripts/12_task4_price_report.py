#!/usr/bin/env python
"""Task 4: run sql/queries/14_price_report.sql -- the SAME query text --
twice: once for March 2024, once for the latest month actually present in
the dataset (computed from the data, never hard-coded). Only the
start_date/end_date parameters differ between the two calls.

Writes reports/task4/price_report_output.txt (full run) and
reports/task4/price_comparison.csv (March vs. latest-month, per product).
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analytics.dims import build_price_revisions_dim
from app.db import connect as pg_connect
from app.duck import connect

HERE = Path(__file__).resolve().parent.parent
REPORT_DIR = HERE / "reports" / "task4"
QUERY_SQL = (HERE / "sql" / "queries" / "14_price_report.sql").read_text()


class Tee:
    def __init__(self, path: Path):
        self.file = open(path, "w")

    def write(self, s: str) -> None:
        sys.__stdout__.write(s)
        self.file.write(s)
        self.file.flush()

    def flush(self) -> None:
        sys.__stdout__.flush()
        self.file.flush()


def month_bounds(any_date: datetime.date) -> tuple[str, str]:
    start = any_date.replace(day=1)
    if start.month == 12:
        next_start = start.replace(year=start.year + 1, month=1)
    else:
        next_start = start.replace(month=start.month + 1)
    end = next_start - datetime.timedelta(days=1)
    return start.isoformat(), end.isoformat()


def run_price_report(con, start_date: str, end_date: str):
    return con.execute(QUERY_SQL, {"start_date": start_date, "end_date": end_date}).fetchdf()


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    sys.stdout = Tee(REPORT_DIR / "price_report_output.txt")

    with pg_connect() as pg:
        build_price_revisions_dim(pg)  # idempotent snapshot refresh from PostgreSQL

    con = connect()

    print("Task 4: price-as-of-reporting-period report")
    print("Query file: sql/queries/14_price_report.sql (identical SQL text both runs)\n")

    # Reporting period #1: March 2024 (given by the task).
    march_start, march_end = "2024-03-01", "2024-03-31"

    # Reporting period #2: the latest month actually present in the curated
    # data -- computed, not hard-coded.
    latest_date = con.execute("SELECT MAX(business_date) FROM fact_sales_dashboard").fetchone()[0]
    latest_start, latest_end = month_bounds(latest_date)

    print(f"Run 1 period: {march_start} .. {march_end}")
    print(f"Run 2 period: {latest_start} .. {latest_end}  (latest month in dataset, "
          f"derived from MAX(business_date)={latest_date})\n")

    march_df = run_price_report(con, march_start, march_end)
    latest_df = run_price_report(con, latest_start, latest_end)

    print(f"=== RUN 1 (March {march_start[:7]}) ===")
    print(f"products reported:             {len(march_df)}")
    print(f"price missing for period:      {int(march_df['price_missing_for_period'].sum())}")
    print(f"price changed during period:   {int(march_df['price_changed_during_period'].sum())}")
    print(march_df.head(5).to_string(index=False))

    print(f"\n=== RUN 2 (latest month {latest_start[:7]}) ===")
    print(f"products reported:             {len(latest_df)}")
    print(f"price missing for period:      {int(latest_df['price_missing_for_period'].sum())}")
    print(f"price changed during period:   {int(latest_df['price_changed_during_period'].sum())}")
    print(latest_df.head(5).to_string(index=False))

    # Comparison: products whose applicable price differs between the two periods.
    comparison = march_df[["product_sk", "product_code", "product_name", "applicable_price"]].rename(
        columns={"applicable_price": "march_price"}
    ).merge(
        latest_df[["product_sk", "applicable_price"]].rename(columns={"applicable_price": "latest_month_price"}),
        on="product_sk",
    )
    changed = comparison[
        comparison["march_price"].notna() & comparison["latest_month_price"].notna()
        & (comparison["march_price"] != comparison["latest_month_price"])
    ].sort_values("product_sk")

    print(f"\n=== Products whose applicable price differs between the two periods: {len(changed)} ===")
    print(changed.head(10).to_string(index=False))

    comparison.to_csv(REPORT_DIR / "price_comparison.csv", index=False)

    # Highlight one concrete real example for the README.
    example = changed.iloc[0]
    print("\n=== Headline example ===")
    print(f"product:            {example['product_name']} ({example['product_code']}, product_sk={example['product_sk']})")
    print(f"March price:        {example['march_price']}")
    print(f"latest-month price: {example['latest_month_price']}")

    sys.stdout = sys.__stdout__
    print(f"Full log written to {REPORT_DIR / 'price_report_output.txt'}")
    print(f"CSV written to {REPORT_DIR / 'price_comparison.csv'}")


if __name__ == "__main__":
    main()
