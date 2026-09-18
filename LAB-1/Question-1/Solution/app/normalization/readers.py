"""Dialect-aware source readers.

Three till-software generations, confirmed against the actual files in
Question-1/data/sales (see reports/inspection_report.md section 2):

  S01-S05  comma CSV,      header bill_no,line_no,product_code,qty,unit_price,line_type,ts
                            ts = ISO-8601 local wall-clock, e.g. 2024-01-01T14:19:31
  S06-S09  semicolon CSV,  header bill_no;line_no;item_code;quantity;rate;type;txn_time
                            txn_time = dd-mm-yyyy HH:MM:SS
  S10-S12  comma CSV,      UTF-8 BOM, header order ts,bill_no,line_no,line_type,product_code,unit_price,qty
                            ts = Unix epoch seconds. Empirically (see inspection report /
                            hour-of-day histogram check) treating the epoch as a plain UTC
                            instant, with NO further timezone shift, reproduces the same
                            realistic store-hours pattern (~9am-9pm) seen in S01-S09 -- so no
                            additional offset is applied here.

Readers are dispatched by file extension first (csv/parquet) and then by
column-name detection (never by fixed column *position*, since S10-S12's
column order does not match the vendor's own notes -- see inspection report).
Every reader produces the same canonical column set:

  bill_no, line_no, product_code, qty, unit_price, line_type, transaction_ts
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

CANONICAL_COLUMNS = [
    "bill_no", "line_no", "product_code", "qty", "unit_price", "line_type", "transaction_ts",
]

_DIALECT_A_COLS = {"bill_no", "line_no", "product_code", "qty", "unit_price", "line_type", "ts"}
_DIALECT_B_COLS = {"bill_no", "line_no", "item_code", "quantity", "rate", "type", "txn_time"}
_DIALECT_C_COLS = {"bill_no", "line_no", "product_code", "qty", "unit_price", "line_type", "ts"}


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    df["line_no"] = df["line_no"].astype("int64")
    df["qty"] = df["qty"].astype("int64")
    df["unit_price"] = df["unit_price"].astype("float64")
    df["bill_no"] = df["bill_no"].astype(str)
    df["product_code"] = df["product_code"].astype(str)
    df["line_type"] = df["line_type"].astype(str)
    return df[CANONICAL_COLUMNS]


def _read_dialect_a(path: Path) -> pd.DataFrame:
    """S01-S05: comma, ISO timestamps."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df = df.rename(columns={"ts": "transaction_ts"})
    df["transaction_ts"] = pd.to_datetime(df["transaction_ts"], format="%Y-%m-%dT%H:%M:%S")
    return _finish(df)


def _read_dialect_b(path: Path) -> pd.DataFrame:
    """S06-S09: semicolon, dd-mm-yyyy HH:MM:SS."""
    df = pd.read_csv(path, sep=";", dtype=str, keep_default_na=False)
    df = df.rename(columns={
        "item_code": "product_code",
        "quantity": "qty",
        "rate": "unit_price",
        "type": "line_type",
        "txn_time": "transaction_ts",
    })
    df["transaction_ts"] = pd.to_datetime(df["transaction_ts"], format="%d-%m-%Y %H:%M:%S")
    return _finish(df)


def _read_dialect_c(path: Path) -> pd.DataFrame:
    """S10-S12: comma, UTF-8 BOM, epoch-second ts, non-standard column order."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df["transaction_ts"] = pd.to_datetime(
        df["ts"].astype("int64"), unit="s", utc=True,
    ).dt.tz_localize(None)
    df = df.drop(columns=["ts"])
    return _finish(df)


def _read_parquet(path: Path) -> pd.DataFrame:
    """Forward-compatible path for the Parquet format the vendor notes mention.

    Not exercised against the supplied dataset (it contains no .parquet files),
    but kept so the reader abstraction does not need to change if a future
    drop includes them. Assumes the same canonical-ish column names as
    dialect A, since Parquet carries typed columns already.
    """
    table = pq.read_table(path)
    df = table.to_pandas()
    rename = {"ts": "transaction_ts", "item_code": "product_code",
              "quantity": "qty", "rate": "unit_price", "type": "line_type",
              "txn_time": "transaction_ts"}
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    if not pd.api.types.is_datetime64_any_dtype(df["transaction_ts"]):
        df["transaction_ts"] = pd.to_datetime(df["transaction_ts"])
    return _finish(df)


def detect_dialect(store_id: str) -> str:
    n = int(store_id[1:])
    if 1 <= n <= 5:
        return "A"
    if 6 <= n <= 9:
        return "B"
    if 10 <= n <= 12:
        return "C"
    raise ValueError(f"unknown store id {store_id!r}, no known dialect")


def read_source(path, store_id: str, ext: str) -> pd.DataFrame:
    """Read one raw source file into the canonical row schema.

    `path` is a filesystem Path, or an io.BytesIO holding the object's bytes
    (used when reading straight out of the MinIO raw layer instead of local
    disk) -- both are accepted transparently by pandas.
    """
    if ext == "parquet":
        return _read_parquet(path)
    dialect = detect_dialect(store_id)
    if hasattr(path, "seek"):
        path.seek(0)
    header = pd.read_csv(path, sep=None, engine="python", nrows=0, encoding="utf-8-sig").columns
    header_set = {c.strip() for c in header}
    if hasattr(path, "seek"):
        path.seek(0)
    if dialect == "A":
        if not _DIALECT_A_COLS.issubset(header_set):
            raise ValueError(f"{path}: expected dialect-A columns, got {header_set}")
        return _read_dialect_a(path)
    if dialect == "B":
        if not _DIALECT_B_COLS.issubset(header_set):
            raise ValueError(f"{path}: expected dialect-B columns, got {header_set}")
        return _read_dialect_b(path)
    if dialect == "C":
        if not _DIALECT_C_COLS.issubset(header_set):
            raise ValueError(f"{path}: expected dialect-C columns, got {header_set}")
        return _read_dialect_c(path)
    raise ValueError(f"unhandled dialect {dialect!r}")
