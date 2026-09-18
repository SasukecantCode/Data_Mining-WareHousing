"""MinIO (S3-compatible) helper built on the minio SDK."""
from __future__ import annotations

import io
from typing import Iterable, Iterator

from minio import Minio
from minio.error import S3Error

from app.config import SETTINGS


def client() -> Minio:
    return Minio(
        SETTINGS.minio_endpoint,
        access_key=SETTINGS.minio_access_key,
        secret_key=SETTINGS.minio_secret_key,
        secure=SETTINGS.minio_secure,
    )


def ensure_bucket(c: Minio | None = None) -> None:
    c = c or client()
    if not c.bucket_exists(SETTINGS.minio_bucket):
        c.make_bucket(SETTINGS.minio_bucket)


def put_file(local_path: str, object_name: str, c: Minio | None = None) -> None:
    c = c or client()
    c.fput_object(SETTINGS.minio_bucket, object_name, local_path)


def put_bytes(data: bytes, object_name: str, content_type: str = "application/octet-stream",
              c: Minio | None = None) -> None:
    c = c or client()
    c.put_object(
        SETTINGS.minio_bucket, object_name, io.BytesIO(data), length=len(data),
        content_type=content_type,
    )


def object_exists(object_name: str, c: Minio | None = None) -> bool:
    c = c or client()
    try:
        c.stat_object(SETTINGS.minio_bucket, object_name)
        return True
    except S3Error as e:
        if e.code in ("NoSuchKey", "NoSuchObject"):
            return False
        raise


def list_objects(prefix: str, c: Minio | None = None) -> Iterator:
    c = c or client()
    return c.list_objects(SETTINGS.minio_bucket, prefix=prefix, recursive=True)


def get_bytes(object_name: str, c: Minio | None = None) -> bytes:
    c = c or client()
    resp = c.get_object(SETTINGS.minio_bucket, object_name)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()


def s3_uri(object_name: str) -> str:
    return f"s3://{SETTINGS.minio_bucket}/{object_name}"
