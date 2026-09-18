#!/usr/bin/env python
"""Task 3: run all dashboard-layer SQL files (sql/queries/08-13) and print results."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.duck import connect

HERE = Path(__file__).resolve().parent.parent


def main() -> None:
    con = connect()
    query_dir = HERE / "sql" / "queries"
    files = sorted(query_dir.glob("*.sql"))
    files = [f for f in files if int(f.name.split("_")[0]) >= 8]
    for sql_file in files:
        print("=" * 78)
        print(sql_file.name)
        print("=" * 78)
        df = con.execute(sql_file.read_text()).fetchdf()
        print(df.to_string(index=False))
        print()


if __name__ == "__main__":
    main()
