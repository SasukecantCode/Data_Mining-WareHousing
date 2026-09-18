"""Task 2: the idempotent loading step, end to end.

"Loading" here means both raw landing (MinIO raw/) and curation (dedup +
enrichment + MinIO curated/) -- together they are what has to satisfy
`load once == load twice == load three times`. Master data (masters.sql)
is not part of "the loading step" being tested here; it is static reference
data loaded once by scripts/01_load_masters.py.

Idempotency comes from two independent mechanisms (Task 1 already built
both; this module is the explicit, testable entrypoint for them):

1. File level (`app.ingestion.raw_landing`): each source file's sha256 is
   recorded in `ingestion_manifest`. Landing the same bytes twice is a
   no-op -- it neither re-uploads nor re-processes the file.
2. Business-line level (`app.deduplication.dedup`): every load groups ALL
   files for a store-day (original + every resend currently in the
   manifest) and unions them by (bill_no, line_no), so the curated Parquet
   for a store-month is rebuilt fresh from the complete set of known lines
   every time, and written to the SAME deterministic object key
   (`curated/sales/store=../year=../month=../sales.parquet`) -- overwritten,
   never appended. That is what makes "latest file wins" unnecessary and
   wrong: a rerun doesn't pick a winner between files, it re-derives the
   full union every time.
"""
from __future__ import annotations

import psycopg

from app import object_store
from app.analytics.curate import curate_all
from app.analytics.dims import build_dims
from app.config import SETTINGS
from app.ingestion.raw_landing import LandingStats, land_all


def load(sales_dir, conn: psycopg.Connection) -> LandingStats:
    """The full idempotent loading step: land raw files, then rebuild the
    curated layer from the current complete manifest."""
    stats = land_all(sales_dir, conn)
    curate_all(conn)
    build_dims(conn, "2024-01-01", "2024-12-31")
    return stats


def reset_destination(conn: psycopg.Connection) -> None:
    """Wipe everything the loading step owns -- raw/curated objects in MinIO
    and the ingestion/audit tables in PostgreSQL -- WITHOUT touching the
    master data (stores/product_categories/products/price_revisions), which
    is not part of "the loading step". Used once, before a fresh 3-run test.
    """
    c = object_store.client()
    object_store.ensure_bucket(c)
    for prefix in ("raw/", "curated/"):
        for obj in list(object_store.list_objects(prefix, c=c)):
            c.remove_object(SETTINGS.minio_bucket, obj.object_name)

    cur = conn.cursor()
    for table in ("dedup_conflicts", "enrichment_rejects", "curation_runs", "ingestion_manifest"):
        cur.execute(f"TRUNCATE TABLE {table}")
