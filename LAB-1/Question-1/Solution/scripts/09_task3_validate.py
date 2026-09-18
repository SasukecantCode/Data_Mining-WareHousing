#!/usr/bin/env python
"""Task 3 required validation (8 checks), run against the actual dataset."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import connect as pg_connect
from app.duck import connect as duck_connect

HERE = Path(__file__).resolve().parent.parent
REPORT_DIR = HERE / "reports" / "task3"


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


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    sys.stdout = Tee(REPORT_DIR / "validation_output.txt")

    con = duck_connect()
    checks = []

    def check(name, passed, detail):
        checks.append((name, passed, detail))
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")

    print("=== Task 3 validation ===\n")

    # 1. reissued product_code resolves to different product_sk before/after 2024-06-01
    rows = con.execute(
        "SELECT product_sk, category_id, MIN(business_date) mn, MAX(business_date) mx "
        "FROM fact_sales WHERE product_code = 'P108206' GROUP BY product_sk, category_id ORDER BY mn"
    ).fetchdf()
    ok = (len(rows) == 2 and str(rows.iloc[0]["mx"])[:10] == "2024-05-31"
          and str(rows.iloc[1]["mn"])[:10] == "2024-06-01" and rows.iloc[0]["product_sk"] != rows.iloc[1]["product_sk"])
    check("1_reissued_code_resolves_differently_before_after_2024-06-01", ok,
          f"P108206 -> {len(rows)} distinct product_sk, split {rows.iloc[0]['mx'] if len(rows) else '?'}/"
          f"{rows.iloc[1]['mn'] if len(rows) > 1 else '?'}")

    # 2. SALE + VOID nets to zero for a real cancelled bill
    net = con.execute(
        "SELECT SUM(revenue_amount) FROM fact_sales WHERE bill_no = 'S01/20240102/00017'"
    ).fetchone()[0]
    check("2_sale_plus_void_nets_to_zero", abs(float(net)) < 1e-6,
          f"S01/20240102/00017 net revenue = {net}")

    # 3. TAX and TENDER contribute zero revenue
    tt = con.execute(
        "SELECT line_type, SUM(revenue_amount) FROM fact_sales WHERE line_type IN ('TAX','TENDER') "
        "GROUP BY line_type"
    ).fetchdf()
    check("3_tax_and_tender_zero_revenue", (tt["sum(revenue_amount)"].astype(float) == 0.0).all(),
          f"{dict(zip(tt['line_type'], tt['sum(revenue_amount)']))}")

    # 4. DISCOUNT reduces revenue but is never assigned a product_sk
    disc = con.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(revenue_amount),0) rev, "
        "SUM(CASE WHEN product_sk IS NOT NULL THEN 1 ELSE 0 END) with_product "
        "FROM fact_sales WHERE line_type = 'DISCOUNT'"
    ).fetchone()
    check("4_discount_reduces_revenue_without_product_identity",
          disc[2] == 0 and float(disc[1]) < 0,
          f"{disc[0]} DISCOUNT lines, total revenue={disc[1]}, "
          f"{disc[2]} of them have a (falsely assigned) product_sk (expected 0)")

    # 5. product_code alone never determines product identity
    n_dup_codes = con.execute(
        "SELECT COUNT(*) FROM (SELECT product_code FROM dim_product GROUP BY product_code HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    check("5_product_code_alone_is_not_a_unique_identity", n_dup_codes > 0,
          f"{n_dup_codes} product_code values map to more than one product_sk in dim_product "
          f"(naive product_code-only join would fan these rows out)")

    # 6. fact row count matches the unique Task 2 business-line dataset
    fact_count = con.execute("SELECT COUNT(*) FROM fact_sales_dashboard").fetchone()[0]
    task2_csv = REPORT_DIR.parent / "task2" / "idempotency_results.csv"
    expected = None
    if task2_csv.exists():
        import csv as csv_mod
        with open(task2_csv) as f:
            expected = int(next(csv_mod.DictReader(f))["row_count"])
    check("6_fact_row_count_matches_task2_dedup_dataset", expected is not None and fact_count == expected,
          f"fact_sales_dashboard rows={fact_count}, Task 2 idempotency_results.csv row_count={expected}")

    # 7. descriptive attributes live in dimensions, not the fact table
    fact_cols = set(con.execute("DESCRIBE fact_sales_dashboard").fetchdf()["column_name"])
    descriptive = {"store_name", "city", "state", "region", "product_name", "brand",
                   "category_name", "department", "address"}
    leaked = fact_cols & descriptive
    check("7_descriptive_attributes_not_duplicated_on_fact_rows", len(leaked) == 0,
          f"fact_sales_dashboard columns: {sorted(fact_cols)}; "
          f"descriptive columns found on the fact grain (expected none): {sorted(leaked) or 'none'}")

    # 8. revenue reconciles with the folder truth for months where source data exists
    import json
    from app.config import SETTINGS
    truth_dir = SETTINGS.source_data_dir
    if not truth_dir.is_absolute():
        truth_dir = (HERE / truth_dir).resolve()
    truth = json.loads((truth_dir / "_truth" / "truth.json").read_text())
    computed = con.execute(
        "SELECT strftime(business_date,'%Y-%m') AS ym, ROUND(SUM(revenue_amount),2) AS rev "
        "FROM fact_sales_dashboard GROUP BY 1"
    ).fetchdf()
    computed_map = dict(zip(computed["ym"], computed["rev"].astype(float)))
    mismatches = {
        m: (computed_map.get(m), truth["monthly_net_revenue_in_folder"][m])
        for m in truth["monthly_net_revenue_in_folder"]
        if abs(computed_map.get(m, -1) - truth["monthly_net_revenue_in_folder"][m]) > 0.01
    }
    check("8_revenue_reconciles_with_folder_truth_all_12_months", len(mismatches) == 0,
          f"{len(truth['monthly_net_revenue_in_folder'])} months checked, {len(mismatches)} mismatches: {mismatches or 'none'}")

    print()
    all_passed = all(p for _, p, _ in checks)
    print(f"TASK 3 VALIDATION: {'PASS' if all_passed else 'FAIL'} ({sum(p for _,p,_ in checks)}/{len(checks)} checks passed)")

    sys.stdout = sys.__stdout__
    print(f"\nFull log written to {REPORT_DIR / 'validation_output.txt'}")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    with pg_connect():  # not strictly needed here, kept for symmetry with other scripts
        main()
