"""Parsing of the source file naming contract.

    SALES_<store_id>_<YYYYMMDD>.<csv|parquet>
    SALES_<store_id>_<YYYYMMDD>__R<n>.<csv|parquet>

The date in the name is the *business date* per the vendor handover notes
(billing_notes.md) -- it must never be derived from timestamps inside the
file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

_PATTERN = re.compile(
    r"^SALES_(?P<store_id>S\d{2})_(?P<yyyymmdd>\d{8})(?:__R(?P<resend>\d+))?\.(?P<ext>csv|parquet)$"
)


@dataclass(frozen=True)
class SourceFileMeta:
    path: Path
    store_id: str
    business_date: date
    resend_seq: int  # 0 = original
    ext: str

    @property
    def is_resend(self) -> bool:
        return self.resend_seq > 0


class InvalidSourceFileName(ValueError):
    pass


def parse_filename(path: Path) -> SourceFileMeta:
    m = _PATTERN.match(path.name)
    if not m:
        raise InvalidSourceFileName(f"does not match SALES_<store>_<YYYYMMDD>[__R<n>].<ext>: {path.name}")
    y, mo, d = m["yyyymmdd"][0:4], m["yyyymmdd"][4:6], m["yyyymmdd"][6:8]
    try:
        bdate = date(int(y), int(mo), int(d))
    except ValueError as e:
        raise InvalidSourceFileName(f"invalid business date in {path.name}: {e}") from e
    return SourceFileMeta(
        path=path,
        store_id=m["store_id"],
        business_date=bdate,
        resend_seq=int(m["resend"]) if m["resend"] else 0,
        ext=m["ext"],
    )


VALID_STORE_IDS = {f"S{i:02d}" for i in range(1, 13)}
