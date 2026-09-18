#!/usr/bin/env python
"""Phase 10/12: run all required analytical SQL files against DuckDB
(curated Parquet in MinIO) and print the results."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.duck import connect

HERE = Path(__file__).resolve().parent.parent


def main() -> None:
    con = connect()
    query_dir = HERE / "sql" / "queries"
    for sql_file in sorted(query_dir.glob("*.sql")):
        print("=" * 78)
        print(sql_file.name)
        print("=" * 78)
        df = con.execute(sql_file.read_text()).fetchdf()
        print(df.to_string(index=False))
        print()


if __name__ == "__main__":
    main()
