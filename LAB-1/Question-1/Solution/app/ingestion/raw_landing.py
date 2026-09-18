"""Phase 4: land source files into MinIO's raw layer, unchanged, one-for-one.

  annapurna/raw/sales/store=<S..>/business_date=<YYYY-MM-DD>/<original filename>

Idempotent: a source file's sha256 checksum is recorded in
ingestion_manifest (PostgreSQL). Re-running with the same input files is a
no-op (skips upload + manifest write) -- see tests/test_idempotency.py.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import psycopg

from app import object_store
from app.normalization.filenames import VALID_STORE_IDS, InvalidSourceFileName, parse_filename


@dataclass
class LandingStats:
    scanned: int = 0
    landed: int = 0
    skipped_already_landed: int = 0
    invalid_filenames: list[str] = None

    def __post_init__(self):
        if self.invalid_filenames is None:
            self.invalid_filenames = []


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _raw_object_name(store_id: str, business_date, filename: str) -> str:
    return f"raw/sales/store={store_id}/business_date={business_date.isoformat()}/{filename}"


def discover_source_files(sales_dir: Path) -> list[Path]:
    files = []
    for p in sorted(sales_dir.iterdir()):
        if not p.is_file():
            continue
        if p.name.endswith(":Zone.Identifier"):
            continue
        files.append(p)
    return files


def land_all(sales_dir: Path, conn: psycopg.Connection) -> LandingStats:
    stats = LandingStats()
    c = object_store.client()
    object_store.ensure_bucket(c)

    cur = conn.cursor()
    cur.execute("SELECT source_file, checksum FROM ingestion_manifest")
    already = dict(cur.fetchall())

    for path in discover_source_files(sales_dir):
        stats.scanned += 1
        try:
            meta = parse_filename(path)
        except InvalidSourceFileName as e:
            stats.invalid_filenames.append(str(e))
            continue
        if meta.store_id not in VALID_STORE_IDS:
            stats.invalid_filenames.append(f"unknown store_id in {path.name}")
            continue

        checksum = _sha256(path)
        if already.get(path.name) == checksum:
            stats.skipped_already_landed += 1
            continue

        object_name = _raw_object_name(meta.store_id, meta.business_date, path.name)
        object_store.put_file(str(path), object_name, c=c)

        raw_bytes = path.stat().st_size
        with open(path, "r", encoding="utf-8-sig") as f:
            raw_row_count = sum(1 for _ in f) - 1  # minus header

        cur.execute(
            """
            INSERT INTO ingestion_manifest
                (source_file, store_id, business_date, resend_seq, checksum,
                 raw_bytes, raw_row_count, raw_object_name)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (source_file) DO UPDATE SET
                checksum = EXCLUDED.checksum,
                raw_bytes = EXCLUDED.raw_bytes,
                raw_row_count = EXCLUDED.raw_row_count,
                raw_object_name = EXCLUDED.raw_object_name,
                landed_at = now()
            """,
            (path.name, meta.store_id, meta.business_date, meta.resend_seq,
             checksum, raw_bytes, raw_row_count, object_name),
        )
        stats.landed += 1

    return stats
