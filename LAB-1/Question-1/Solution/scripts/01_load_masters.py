#!/usr/bin/env python
"""Phase 3: load the vendor-supplied masters.sql into PostgreSQL as-is, then
layer our own audit/lineage tables on top (sql/schema/audit.sql)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import SETTINGS
from app.db import connect

HERE = Path(__file__).resolve().parent.parent


def main() -> None:
    masters_sql = SETTINGS.source_masters_sql.read_text()
    audit_sql = (HERE / "sql" / "schema" / "audit.sql").read_text()

    with connect() as conn:
        cur = conn.cursor()
        print(f"Loading {SETTINGS.source_masters_sql} ...")
        cur.execute(masters_sql)
        print("Creating audit/lineage tables ...")
        cur.execute(audit_sql)

        for table in ["stores", "product_categories", "products", "price_revisions"]:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            print(f"  {table}: {cur.fetchone()[0]} rows")

    print("Master data loaded.")


if __name__ == "__main__":
    main()
