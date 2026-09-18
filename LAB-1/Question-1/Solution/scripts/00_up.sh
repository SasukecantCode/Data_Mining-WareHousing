#!/usr/bin/env bash
# Phase 2: start PostgreSQL + MinIO and wait for both to report healthy.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || cp .env.example .env

docker compose up -d

echo "Waiting for postgres + minio to become healthy..."
for i in $(seq 1 30); do
  pg_status=$(docker inspect -f '{{.State.Health.Status}}' annapurna_postgres 2>/dev/null || echo "starting")
  minio_status=$(docker inspect -f '{{.State.Health.Status}}' annapurna_minio 2>/dev/null || echo "starting")
  if [ "$pg_status" = "healthy" ] && [ "$minio_status" = "healthy" ]; then
    echo "postgres: $pg_status, minio: $minio_status"
    exit 0
  fi
  sleep 2
done

echo "Timed out waiting for containers to become healthy." >&2
docker compose ps
exit 1
