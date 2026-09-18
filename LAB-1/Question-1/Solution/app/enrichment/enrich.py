"""Temporal enrichment: resolve product_sk and historical_authoritative_price.

Both resolutions are "as of business_date" interval joins against PostgreSQL
master data (masters.sql), done ONCE during curated-layer build -- never at
analyst-query time (task requirement: "Do not join historical product data by
product_code during every analyst query").

Rows whose product_code is one of the pseudo-codes the till uses for
non-product lines ('DISC', 'TAX', 'TENDER') never go through product/price
resolution -- confirmed against real rows, including the edge case of a
VOID line that cancels a DISCOUNT line (product_code is still 'DISC' even
though line_type='VOID'), so eligibility is decided by product_code, not by
line_type. A line whose product_code looks like a real product code but
still fails to match any products row (or matches more than one) is a
genuine data-quality problem: it is logged to `enrichment_rejects` for audit
but its row is NOT dropped from the curated fact table -- revenue must stay
complete even when enrichment is incomplete (task requirement: "Keep
rejected/conflicting records visible rather than silently dropping them").
"""
from __future__ import annotations

import pandas as pd
import psycopg

NON_PRODUCT_CODES = {"DISC", "TAX", "TENDER"}


def load_products(conn: psycopg.Connection) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT product_sk, product_code, category_id, valid_from, valid_to "
        "FROM products",
        conn,
    )
    df["valid_from"] = pd.to_datetime(df["valid_from"]).dt.date
    df["valid_to"] = pd.to_datetime(df["valid_to"]).dt.date
    return df


def load_price_revisions(conn: psycopg.Connection) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT product_sk, selling_price, effective_from, effective_to "
        "FROM price_revisions",
        conn,
    )
    df["effective_from"] = pd.to_datetime(df["effective_from"]).dt.date
    df["effective_to"] = pd.to_datetime(df["effective_to"]).dt.date
    return df


def resolve_product_sk(
    sales: pd.DataFrame, products: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (resolved, rejects). `resolved` has the SAME row count as `sales`
    -- every input row survives, revenue stays complete. `sales` must have a
    unique `_row_id` column, `product_code` and `business_date` (python
    date). Resolution is product_code + business_date BETWEEN valid_from AND
    valid_to -> product_sk. `rejects` lists rows whose product_code looked
    like a real product code but could not be resolved (audit only).
    """
    is_product_line = ~sales["product_code"].isin(NON_PRODUCT_CODES)
    candidates = sales.loc[is_product_line, ["_row_id", "product_code", "business_date"]]
    non_product = sales.loc[~is_product_line].copy()
    non_product["product_sk"] = pd.NA
    non_product["category_id"] = pd.NA

    merged = candidates.merge(products, on="product_code", how="left")
    in_range = (merged["business_date"] >= merged["valid_from"]) & (
        merged["business_date"] <= merged["valid_to"]
    )
    matched = merged[in_range].drop(columns=["valid_from", "valid_to"])

    # a product_code + date should resolve to exactly one product_sk; if it
    # somehow resolves to more than one, that is a masters.sql data problem
    # worth surfacing rather than silently picking one.
    dup_mask = matched.duplicated(subset=["_row_id"], keep=False)
    ambiguous = matched[dup_mask]
    matched = matched[~dup_mask].drop_duplicates(subset=["_row_id"])

    resolved_ids = set(matched["_row_id"])
    unmatched_ids = set(candidates["_row_id"]) - resolved_ids - set(ambiguous["_row_id"])
    reject_ids = unmatched_ids | set(ambiguous["_row_id"])

    product_line_rows = sales.loc[sales["_row_id"].isin(resolved_ids)].merge(
        matched[["_row_id", "product_sk", "category_id"]], on="_row_id", how="left"
    )
    unmatched_rows = sales.loc[sales["_row_id"].isin(reject_ids)].copy()
    unmatched_rows["product_sk"] = pd.NA
    unmatched_rows["category_id"] = pd.NA

    rejects = sales.loc[sales["_row_id"].isin(reject_ids)]

    resolved = pd.concat([product_line_rows, non_product, unmatched_rows], ignore_index=True)
    return resolved, rejects


def resolve_historical_price(
    sales: pd.DataFrame, price_revisions: pd.DataFrame
) -> pd.DataFrame:
    """Adds `historical_authoritative_price` resolved from price_revisions
    as-of business_date, keyed by product_sk. Rows with no product_sk (TAX,
    TENDER, DISCOUNT) get a null historical price -- they are not products.
    """
    has_sk = sales["product_sk"].notna()
    with_sk = sales.loc[has_sk].copy()
    without_sk = sales.loc[~has_sk].copy()
    without_sk["historical_authoritative_price"] = pd.NA

    merged = with_sk.merge(price_revisions, on="product_sk", how="left")
    in_range = (merged["business_date"] >= merged["effective_from"]) & (
        merged["business_date"] <= merged["effective_to"]
    )
    matched = merged[in_range].drop(columns=["effective_from", "effective_to"])
    matched = matched.rename(columns={"selling_price": "historical_authoritative_price"})
    matched = matched.drop_duplicates(subset=["_row_id"])

    resolved_ids = set(matched["_row_id"])
    missing = with_sk.loc[~with_sk["_row_id"].isin(resolved_ids)].copy()
    missing["historical_authoritative_price"] = pd.NA

    return pd.concat([matched, missing, without_sk], ignore_index=True)
