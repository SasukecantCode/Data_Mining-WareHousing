"""Central configuration, loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_HERE = Path(__file__).resolve().parent.parent  # LAB-1/
load_dotenv(_HERE / ".env")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    postgres_host: str = _env("POSTGRES_HOST", "localhost")
    postgres_port: int = int(_env("POSTGRES_PORT", "5432"))
    postgres_db: str = _env("POSTGRES_DB", "annapurna")
    postgres_user: str = _env("POSTGRES_USER", "annapurna")
    postgres_password: str = _env("POSTGRES_PASSWORD", "annapurna_dev_password")

    minio_endpoint: str = _env("MINIO_ENDPOINT", "localhost:9000")
    minio_access_key: str = _env("MINIO_ACCESS_KEY", "annapurna_minio")
    minio_secret_key: str = _env("MINIO_SECRET_KEY", "annapurna_minio_secret")
    minio_secure: bool = _env("MINIO_SECURE", "false").lower() == "true"
    minio_bucket: str = _env("MINIO_BUCKET", "annapurna")

    source_data_dir: Path = Path(_env("SOURCE_DATA_DIR", "../data"))

    @property
    def postgres_dsn(self) -> str:
        return (
            f"host={self.postgres_host} port={self.postgres_port} "
            f"dbname={self.postgres_db} user={self.postgres_user} "
            f"password={self.postgres_password}"
        )

    @property
    def source_sales_dir(self) -> Path:
        p = self.source_data_dir
        if not p.is_absolute():
            p = (_HERE / p).resolve()
        return p / "sales"

    @property
    def source_masters_sql(self) -> Path:
        p = self.source_data_dir
        if not p.is_absolute():
            p = (_HERE / p).resolve()
        return p / "masters.sql"


SETTINGS = Settings()
