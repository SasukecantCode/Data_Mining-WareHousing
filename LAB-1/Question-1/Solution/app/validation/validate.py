"""Phase 11: data-quality checks + phase 12/23 reconciliation.

Each check returns (name, passed: bool, detail: str). Nothing here is a
pipeline input -- truth.json / finance_monthly.csv are read only to compare
against, never to derive fact rows.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd
import psycopg

from app.config import SETTINGS


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def _r(name, passed, detail) -> CheckResult:
    return CheckResult(name, bool(passed), detail)


def run_quality_checks(con: duckdb.DuckDBPyConnection, pg: psycopg.Connection) -> list[CheckResult]:
    results = []

    # 1. no duplicate (store_id, bill_no, line_no) in curated fact table
    dup = con.execute(
        "SELECT COUNT(*) FROM (SELECT store_id, bill_no, line_no, COUNT(*) c "
        "FROM fact_sales GROUP BY 1,2,3 HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    results.append(_r("no_duplicate_bill_line_keys", dup == 0, f"{dup} duplicate keys found"))

    # 2. TAX contributes zero revenue
    tax_rev = con.execute("SELECT COALESCE(SUM(revenue_amount),0) FROM fact_sales WHERE line_type='TAX'").fetchone()[0]
    results.append(_r("tax_not_revenue", float(tax_rev) == 0.0, f"sum(revenue_amount) for TAX = {tax_rev}"))

    # 3. TENDER contributes zero revenue
    tender_rev = con.execute("SELECT COALESCE(SUM(revenue_amount),0) FROM fact_sales WHERE line_type='TENDER'").fetchone()[0]
    results.append(_r("tender_not_revenue", float(tender_rev) == 0.0, f"sum(revenue_amount) for TENDER = {tender_rev}"))

    # 4. VOID semantics: a known fully-cancelled bill nets to zero.
    #    S01/20240102/00017 -- confirmed in reports/inspection_report.md.
    bill_net = con.execute(
        "SELECT COALESCE(SUM(revenue_amount),0) FROM fact_sales "
        "WHERE bill_no = 'S01/20240102/00017'"
    ).fetchone()[0]
    results.append(_r("void_cancels_sale_known_bill", abs(float(bill_net)) < 1e-6,
                       f"S01/20240102/00017 net revenue = {bill_net} (expected 0)"))

    # 5. every line that names a REAL product code resolves to a product_sk.
    #    Excludes VOID-of-a-DISCOUNT rows (product_code='DISC', line_type='VOID'
    #    -- a discount line cancelled as part of a whole-bill cancellation is
    #    not a product line even though line_type='VOID'; confirmed real case,
    #    see reports/inspection_report.md).
    unresolved = con.execute(
        "SELECT COUNT(*) FROM fact_sales "
        "WHERE line_type IN ('SALE','RETURN','VOID') "
        "AND product_code NOT IN ('DISC','TAX','TENDER') "
        "AND product_sk IS NULL"
    ).fetchone()[0]
    results.append(_r("product_sk_resolved_for_all_real_item_lines", unresolved == 0,
                       f"{unresolved} SALE/RETURN/VOID rows (excluding VOID-of-DISCOUNT) with no product_sk"))

    # 6. reissued product code resolves to two different product_sk values
    n_products_for_reissue = con.execute(
        "SELECT COUNT(DISTINCT product_sk) FROM fact_sales WHERE product_code = 'P108206'"
    ).fetchone()[0]
    results.append(_r("reissued_code_resolves_to_multiple_products", n_products_for_reissue >= 2,
                       f"P108206 resolves to {n_products_for_reissue} distinct product_sk values"))

    # 7. business_date vs transaction_ts can legitimately differ (midnight-crossing bill)
    mismatch = con.execute(
        "SELECT COUNT(*) FROM fact_sales WHERE date_trunc('day', transaction_ts) <> business_date"
    ).fetchone()[0]
    results.append(_r("business_date_independent_of_timestamp", mismatch > 0,
                       f"{mismatch} rows where transaction_ts calendar date != business_date (expected > 0)"))

    # 8. S07's known 3-day gap has NO fact rows (not fabricated zero-sales rows)
    gap = con.execute(
        "SELECT COUNT(*) FROM fact_sales WHERE store_id='S07' "
        "AND business_date BETWEEN DATE '2024-07-09' AND DATE '2024-07-11'"
    ).fetchone()[0]
    results.append(_r("s07_missing_days_have_no_fabricated_rows", gap == 0,
                       f"{gap} fact rows for S07 2024-07-09..11 (expected 0)"))

    # 9. rerun idempotency: dedup_conflicts / enrichment_rejects tables reflect
    #    the current run only (checked structurally: curation_runs has exactly
    #    one row per store-month)
    cur = pg.cursor()
    cur.execute("SELECT COUNT(*) FROM (SELECT store_id, year, month, COUNT(*) c FROM curation_runs GROUP BY 1,2,3 HAVING COUNT(*)>1) t")
    dup_runs = cur.fetchone()[0]
    results.append(_r("curation_runs_one_row_per_store_month", dup_runs == 0,
                       f"{dup_runs} store-months with duplicate curation_runs rows"))

    return results


def reconcile_with_truth(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    truth_path = SETTINGS.source_data_dir
    if not truth_path.is_absolute():
        truth_path = (Path(__file__).resolve().parent.parent.parent / truth_path).resolve()
    truth = json.loads((truth_path / "_truth" / "truth.json").read_text())
    finance = pd.read_csv(truth_path / "finance_monthly.csv")
    finance["month"] = finance["month"].astype(str)

    computed = con.execute(
        "SELECT strftime(business_date, '%Y-%m') AS month, ROUND(SUM(revenue_amount),2) AS pos_folder_revenue "
        "FROM fact_sales GROUP BY 1 ORDER BY 1"
    ).fetchdf()

    truth_in_folder = pd.Series(truth["monthly_net_revenue_in_folder"], name="truth_in_folder").rename_axis("month").reset_index()
    truth_full = pd.Series(truth["monthly_net_revenue"], name="truth_full_scope").rename_axis("month").reset_index()

    out = computed.merge(truth_in_folder, on="month", how="left") \
                   .merge(truth_full, on="month", how="left") \
                   .merge(finance[["month", "revenue_inr"]].rename(columns={"revenue_inr": "finance_signed_off"}), on="month", how="left")
    out["matches_truth_in_folder"] = (out["pos_folder_revenue"] - out["truth_in_folder"]).abs() < 0.01
    out["finance_note"] = out["month"].map(truth.get("finance_notes", {}))
    return out
