#!/usr/bin/env python
"""Phase 4: land every source sales file into MinIO's raw layer, unchanged."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import SETTINGS
from app.db import connect
from app.ingestion.raw_landing import land_all


def main() -> None:
    t0 = time.time()
    with connect() as conn:
        stats = land_all(SETTINGS.source_sales_dir, conn)
    dt = time.time() - t0

    print(f"Scanned:                 {stats.scanned}")
    print(f"Landed (new/changed):    {stats.landed}")
    print(f"Skipped (already landed):{stats.skipped_already_landed}")
    print(f"Invalid filenames:       {len(stats.invalid_filenames)}")
    for msg in stats.invalid_filenames[:10]:
        print(f"  - {msg}")
    print(f"Elapsed: {dt:.1f}s")


if __name__ == "__main__":
    main()
