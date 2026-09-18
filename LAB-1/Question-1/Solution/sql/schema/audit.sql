-- Audit / lineage / idempotency tables, layered on top of the vendor-supplied
-- masters.sql schema. Not part of the source master data -- created and owned
-- by this pipeline.

CREATE TABLE IF NOT EXISTS ingestion_manifest (
    source_file     TEXT PRIMARY KEY,       -- e.g. SALES_S01_20240102__R1.csv
    store_id        TEXT NOT NULL,
    business_date   DATE NOT NULL,
    resend_seq      INT NOT NULL,           -- 0 = original
    checksum        TEXT NOT NULL,          -- sha256 of file bytes
    raw_bytes       BIGINT NOT NULL,
    raw_row_count   INT NOT NULL,
    raw_object_name TEXT NOT NULL,          -- MinIO object key under raw/
    landed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_manifest_store_date ON ingestion_manifest(store_id, business_date);

CREATE TABLE IF NOT EXISTS dedup_conflicts (
    id                BIGSERIAL PRIMARY KEY,
    store_id          TEXT NOT NULL,
    business_date     DATE NOT NULL,
    bill_no           TEXT NOT NULL,
    line_no           INT NOT NULL,
    prior_source_file TEXT,
    prior_line_type   TEXT,
    new_source_file   TEXT,
    new_line_type     TEXT,
    detected_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS enrichment_rejects (
    id             BIGSERIAL PRIMARY KEY,
    store_id       TEXT NOT NULL,
    business_date  DATE NOT NULL,
    bill_no        TEXT NOT NULL,
    line_no        INT NOT NULL,
    product_code   TEXT,
    reason         TEXT NOT NULL,           -- e.g. 'no_product_match', 'ambiguous_product_match'
    detected_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS curation_runs (
    run_id              BIGSERIAL PRIMARY KEY,
    store_id            TEXT NOT NULL,
    year                INT NOT NULL,
    month               INT NOT NULL,
    input_row_count     INT NOT NULL,       -- raw lines across all source files for this store-month
    output_row_count    INT NOT NULL,       -- unique (bill_no,line_no) rows after dedup
    revenue_amount      NUMERIC(18,2) NOT NULL,
    curated_object_name TEXT NOT NULL,
    run_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_curation_store_month ON curation_runs(store_id, year, month);
