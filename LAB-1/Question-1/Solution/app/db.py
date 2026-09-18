"""PostgreSQL connection helper."""
from __future__ import annotations

import contextlib
from typing import Iterator

import psycopg

from app.config import SETTINGS


@contextlib.contextmanager
def connect() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(SETTINGS.postgres_dsn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
