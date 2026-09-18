"""Phase 5-9: normalize -> deduplicate -> enrich -> revenue -> curated Parquet.

Reads raw files back out of MinIO's raw/ layer (not local disk -- the raw
layer is the source of truth for everything downstream), groups them by
(store, business_date), deduplicates each store-day at (bill_no,line_no),
resolves product_sk/historical price as-of business_date, computes revenue,
and writes one Parquet file per (store, year, month) to MinIO's curated/
layer. Idempotent: writing the same store-month again overwrites the same
deterministic object key, it never appends.
"""
from __future__ import annotations

import collections
import io
from dataclasses import dataclass, field
from decimal import Decimal

import pandas as pd
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq

from app import object_store
from app.deduplication.dedup import dedup_store_day
from app.enrichment.enrich import (
    load_price_revisions,
    load_products,
    resolve_historical_price,
    resolve_product_sk,
)
from app.analytics.revenue import compute_revenue
from app.normalization.readers import read_source

FACT_SCHEMA = pa.schema([
    ("bill_no", pa.string()),
    ("line_no", pa.int64()),
    ("store_id", pa.string()),
    ("business_date", pa.date32()),
    ("transaction_ts", pa.timestamp("us")),
    ("product_code", pa.string()),
    ("qty", pa.int64()),
    ("source_unit_price", pa.decimal128(12, 2)),
    ("line_type", pa.string()),
    ("product_sk", pa.int64()),
    ("category_id", pa.string()),
    ("historical_authoritative_price", pa.decimal128(12, 2)),
    ("revenue_amount", pa.decimal128(14, 2)),
    ("source_file", pa.string()),
])


@dataclass
class StoreMonthResult:
    store_id: str
    year: int
    month: int
    input_row_count: int
    output_row_count: int
    revenue_amount: float
    curated_object_name: str
    conflicts: list[dict] = field(default_factory=list)
    rejects: list[dict] = field(default_factory=list)


def fetch_manifest(conn: psycopg.Connection) -> pd.DataFrame:
    return pd.read_sql(
        "SELECT source_file, store_id, business_date, resend_seq, raw_object_name "
        "FROM ingestion_manifest ORDER BY store_id, business_date, resend_seq",
        conn,
    )


def _read_raw_object(object_name: str, store_id: str, ext: str, c) -> pd.DataFrame:
    data = object_store.get_bytes(object_name, c=c)
    return read_source(io.BytesIO(data), store_id, ext)


def build_store_day(
    store_id: str, business_date, files: list[tuple[str, int, str]], c,
) -> tuple[pd.DataFrame, list[dict], int]:
    frames = []
    for source_file, resend_seq, raw_object_name in files:
        ext = source_file.rsplit(".", 1)[-1]
        df = _read_raw_object(raw_object_name, store_id, ext, c)
        frames.append((source_file, resend_seq, df))
    result = dedup_store_day(frames)
    out = result.rows.copy()
    if not out.empty:
        out["store_id"] = store_id
        out["business_date"] = business_date
    for conflict in result.conflicts:
        conflict["store_id"] = store_id
        conflict["business_date"] = business_date
    return out, result.conflicts, result.n_input_rows


def build_store_month(
    store_id: str,
    year: int,
    month: int,
    day_files: dict,  # business_date -> list[(source_file, resend_seq, raw_object_name)]
    products: pd.DataFrame,
    price_revisions: pd.DataFrame,
) -> StoreMonthResult:
    c = object_store.client()
    day_frames = []
    all_conflicts: list[dict] = []
    total_input_rows = 0

    for business_date, files in sorted(day_files.items()):
        day_df, conflicts, n_input = build_store_day(store_id, business_date, files, c)
        total_input_rows += n_input
        if not day_df.empty:
            day_frames.append(day_df)
        all_conflicts.extend(conflicts)

    if day_frames:
        month_df = pd.concat(day_frames, ignore_index=True)
    else:
        month_df = pd.DataFrame(columns=[
            "bill_no", "line_no", "product_code", "qty", "unit_price",
            "line_type", "transaction_ts", "store_id", "business_date", "source_file",
        ])

    month_df = month_df.rename(columns={"unit_price": "source_unit_price"})
    month_df["_row_id"] = range(len(month_df))

    resolved, unresolved = resolve_product_sk(month_df, products)
    rejects = []
    for _, row in unresolved.iterrows():
        rejects.append({
            "store_id": store_id,
            "business_date": row["business_date"],
            "bill_no": row["bill_no"],
            "line_no": int(row["line_no"]),
            "product_code": row["product_code"],
            "reason": "no_or_ambiguous_product_match",
        })

    priced = resolve_historical_price(resolved, price_revisions)
    priced["revenue_amount"] = compute_revenue(priced)
    priced = priced.drop(columns=["_row_id"])

    out_cols = [c.name for c in FACT_SCHEMA]
    for col in out_cols:
        if col not in priced.columns:
            priced[col] = pd.NA
    priced = priced[out_cols].sort_values(["business_date", "bill_no", "line_no"]).reset_index(drop=True)

    def _to_decimal(x):
        return None if pd.isna(x) else Decimal(str(round(float(x), 2)))

    for money_col in ("source_unit_price", "historical_authoritative_price", "revenue_amount"):
        priced[money_col] = priced[money_col].map(_to_decimal)
    priced["product_sk"] = priced["product_sk"].astype("Int64")

    object_name = f"curated/sales/store={store_id}/year={year:04d}/month={month:02d}/sales.parquet"
    table = pa.Table.from_pandas(priced, schema=FACT_SCHEMA, preserve_index=False)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    object_store.put_bytes(buf.getvalue(), object_name, content_type="application/octet-stream", c=c)

    revenue_total = float(priced["revenue_amount"].astype("float64").sum())

    return StoreMonthResult(
        store_id=store_id, year=year, month=month,
        input_row_count=total_input_rows, output_row_count=len(priced),
        revenue_amount=revenue_total, curated_object_name=object_name,
        conflicts=all_conflicts, rejects=rejects,
    )


def group_manifest_by_store_month(manifest: pd.DataFrame):
    """store_id -> (year,month) -> business_date -> list[(source_file,resend_seq,raw_object_name)]"""
    grouped = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
    for row in manifest.itertuples():
        bdate = row.business_date
        grouped[row.store_id][(bdate.year, bdate.month)][bdate].append(
            (row.source_file, row.resend_seq, row.raw_object_name)
        )
    return grouped


def curate_all(conn: psycopg.Connection) -> list[StoreMonthResult]:
    manifest = fetch_manifest(conn)
    products = load_products(conn)
    price_revisions = load_price_revisions(conn)
    grouped = group_manifest_by_store_month(manifest)

    results: list[StoreMonthResult] = []
    for store_id in sorted(grouped):
        for (year, month), day_files in sorted(grouped[store_id].items()):
            res = build_store_month(store_id, year, month, day_files, products, price_revisions)
            results.append(res)
            _persist_audit(conn, res)
    return results


def _next_month(year: int, month: int) -> str:
    if month == 12:
        return f"{year + 1:04d}-01-01"
    return f"{year:04d}-{month + 1:02d}-01"


def _persist_audit(conn: psycopg.Connection, res: StoreMonthResult) -> None:
    cur = conn.cursor()
    # audit tables are rebuilt per store-month on every run, so reruns don't
    # accumulate duplicate audit history for unchanged input files.
    cur.execute(
        "DELETE FROM dedup_conflicts WHERE store_id=%s AND business_date >= %s AND business_date < %s",
        (res.store_id, f"{res.year:04d}-{res.month:02d}-01", _next_month(res.year, res.month)),
    )
    cur.execute(
        "DELETE FROM enrichment_rejects WHERE store_id=%s AND business_date >= %s AND business_date < %s",
        (res.store_id, f"{res.year:04d}-{res.month:02d}-01", _next_month(res.year, res.month)),
    )
    cur.execute(
        "DELETE FROM curation_runs WHERE store_id=%s AND year=%s AND month=%s",
        (res.store_id, res.year, res.month),
    )
    if res.conflicts:
        cur.executemany(
            """INSERT INTO dedup_conflicts
               (store_id, business_date, bill_no, line_no, prior_source_file,
                prior_line_type, new_source_file, new_line_type)
               VALUES (%(store_id)s,%(business_date)s,%(bill_no)s,%(line_no)s,
                       %(prior_source_file)s,%(prior_line_type)s,%(new_source_file)s,%(new_line_type)s)""",
            res.conflicts,
        )
    if res.rejects:
        cur.executemany(
            """INSERT INTO enrichment_rejects
               (store_id, business_date, bill_no, line_no, product_code, reason)
               VALUES (%(store_id)s,%(business_date)s,%(bill_no)s,%(line_no)s,%(product_code)s,%(reason)s)""",
            res.rejects,
        )
    cur.execute(
        """INSERT INTO curation_runs
           (store_id, year, month, input_row_count, output_row_count, revenue_amount, curated_object_name)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (res.store_id, res.year, res.month, res.input_row_count, res.output_row_count,
         res.revenue_amount, res.curated_object_name),
    )
