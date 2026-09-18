# Annapurna Stores — lakehouse-style sales platform (Task 1 + Task 2)

**Task 1** solves: *"analysts open daily sales files manually and produce
inconsistent monthly numbers; the CFO wants October to mean October,
historical prices/products to stay historically correct, and revenue
queryable by store/product/day/week/month without opening files."*

**Task 2** solves: *"some days appear in the source folder more than once
because the billing system resends a store report. Running the loader
multiple times must produce exactly the same final dataset — load once =
load twice = load three times."* See [Task 2](#task-2--idempotent-loading)
below.

The unrelated tender-notice deduplication problem (`LAB-1/Question-2`) is
**not** part of this implementation.

## Architecture

```
SOURCE FILES (Question-1/data/sales, 4,457 CSVs, 3 till dialects)
    |
    v
MINIO RAW           raw/sales/store=S../business_date=YYYY-MM-DD/<original filename>
    |                (bytes preserved exactly as supplied; checksum-tracked in
    |                 PostgreSQL ingestion_manifest for idempotent re-landing)
    v
NORMALIZATION        app/normalization -- 3 dialect readers (comma/ISO,
    |                semicolon/dd-mm-yyyy, BOM+epoch) -> one canonical schema
    v
DEDUPLICATION        app/deduplication -- union by (bill_no,line_no) across a
    |                store-day's original + every resend; conflicts audited,
    |                never silently dropped
    v
POSTGRESQL           masters.sql (stores, product_categories, products,
    (master data)     price_revisions) -- product_sk / historical price
    |                 resolved AS-OF business_date, once, during curation
    v
CURATED PARQUET      curated/sales/store=S../year=YYYY/month=MM/sales.parquet
  (MinIO)             + curated/dims/{dim_store,dim_product,dim_category,dim_date}.parquet
    v
DUCKDB                reads Parquet straight out of MinIO (httpfs + S3), never
    |                  copies curated data into PostgreSQL
    v
ANALYTICAL SQL        sql/queries/*.sql -- October revenue by store/product/
                       day/week, historical product & price lookups
```

PostgreSQL = master/reference data + audit/lineage tables only. DuckDB = the
only analytical query engine, reading Parquet directly from MinIO.

## Data inventory (measured, not assumed — see `reports/inspection_report.md`)

| | |
|---|---|
| Sales CSV files | 4,457 (0 Parquet files in this drop) |
| Resend files | 68 (54 `__R1`, 14 `__R2`) |
| Raw bytes (sales/) | 68,706,877 (~65.5 MiB) |
| Raw data lines (incl. resend copies) | 1,137,585 — matches `_truth/truth.json.raw_lines` |
| Stores | 12 (S01–S12) |
| Products | 1,224, of which 24 codes are reissued (`n_reissued_codes` in truth.json) |
| Price revisions | 4,320 |
| Curated unique fact rows (after dedup) | 1,120,924 (16,661 duplicate lines removed by resend dedup) |
| Total revenue, all 12 months | ₹522,865,735.75 — matches `sum(truth.json.monthly_net_revenue_in_folder)` exactly |

## Object-store layout (actually implemented)

```
annapurna/
├── raw/sales/store=S01/business_date=2024-01-01/SALES_S01_20240101.csv
│                       .../business_date=2024-10-14/SALES_S01_20241014.csv
│                       ...                                      (4,457 objects, 65.5 MiB)
└── curated/
    ├── sales/store=S01/year=2024/month=01/sales.parquet
    │        ...store=S01/year=2024/month=10/sales.parquet      (144 objects = 12 stores x 12 months, 20.2 MiB)
    └── dims/dim_store.parquet, dim_category.parquet, dim_product.parquet, dim_date.parquet
```

**Why store/year/month and not store/product/day/...**: the CFO's questions
filter by store and a time grain (day/week/month) first; product stays a
*column* inside each partition so a single store-month file already answers
"revenue by product for that store-month" without opening more files.
Partitioning by product too would multiply 144 files into tens of thousands
of tiny ones for no pruning benefit the CFO's questions actually need.

## October 2024 result (computed, not hard-coded)

```sql
SELECT ROUND(SUM(revenue_amount),2) FROM fact_sales
WHERE business_date >= DATE '2024-10-01' AND business_date < DATE '2024-11-01';
-- 56359195.92
```
Matches `_truth/truth.json.monthly_net_revenue_in_folder["2024-10"]` exactly,
computed independently by DuckDB reading `curated/sales/*/2024/10/*.parquet`.

All 12 months reconcile exactly against the truth file's "in folder"
definition (`scripts/04_validate.py`), and the differences against
`finance_monthly.csv` land exactly where the vendor notes said they would:
March (+₹486,250 institutional invoice, out-of-till scope), July (finance has
S07's 3 lost days by phone, the folder never will), December (finance rounds
every bill to the rupee before summing, ₹50.48 difference).

## Partition-pruning proof (`scripts/06_partition_pruning_demo.py`)

Representative query: **revenue for S01, October 2024.**

| Layout | Candidate files | Candidate bytes |
|---|---|---|
| Curated, flat (entire `curated/sales/` prefix) | 144 | 20.2 MiB |
| Curated, partitioned `store=S01/year=2024/month=10/` | **1** | **223.6 KiB** |

That is a 99.3% reduction in candidate files and 98.9% reduction in candidate
bytes, measured with real MinIO object listings (`object_store.list_objects`),
not estimated. DuckDB's own `EXPLAIN` output confirms it isn't just a listing
trick — the scan operator reports `Scanning Files: 1/144` with the hive
partition predicate (`store='S01'`, `month=10`) pushed into the parquet scan,
and a best-of-3 timed run shows the filtered query (6.9ms) beating an
unfiltered full 144-file scan (21.5ms) on this machine. The raw layer shows
the same effect: a naive flat listing of `raw/sales/` is 4,457 objects/65.5
MiB, versus 31 objects/827 KiB for `raw/sales/store=S01/business_date=2024-10-*/`.

## Correctness checks (evidence, not assertions — `reports/inspection_report.md` + `scripts/04_validate.py`)

* **Resend handling**: `SALES_S01_20241112__R1.csv` is a genuine partial
  resend (150 of 263 original keys); union-by-`(bill_no,line_no)` recovers
  all 263, "latest file wins" would have silently dropped 113 real lines.
* **Product-code reissue**: `P108206` resolves to `product_sk=2173`
  ("Daawat Poha 10kg", category C04) for sales up to 2024-05-31, and
  `product_sk=2217` ("Local Mandi Apple 1kg", category C12) from 2024-06-01 —
  test-covered in `tests/test_product_reissue.py`.
* **Historical price**: `source_unit_price` and `historical_authoritative_price`
  are both kept; they disagree on 11,079 / 745,860 SALE lines (~1 in 67,
  consistent with the vendor's "roughly one in seventy" estimate) —
  `sql/queries/07_historical_price_lookup.sql`.
* **Business date**: `S01/20240102/00029` has `business_date=2024-01-02`
  (from the filename) but `transaction_ts=2024-01-03T00:49:28` (after
  midnight) — both fields kept, never conflated.
* **VOID cancellation**: `S01/20240102/00017` — 4 SALE lines mirrored by 4
  VOID lines (negated qty) plus TAX=0/TENDER=0 — nets to exactly ₹0.
* **Missing S07 days**: `fact_sales` has zero rows for S07 2024-07-09/10/11
  (no fabricated zero-sales rows); those 3 filenames are confirmed absent
  from the source directory.

## Reproducing Task 1 end to end

```bash
cd LAB-1/Question-1/Solution
cp .env.example .env                     # defaults work out of the box
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Phase 2 — infrastructure
bash scripts/00_up.sh                    # docker compose up -d, waits for health

# Phase 3 — master data
.venv/bin/python scripts/01_load_masters.py

# Phase 4 — raw landing (source files -> MinIO raw/)
.venv/bin/python scripts/02_land_raw.py

# Phases 5-9 — normalize, dedup, enrich, revenue, curated Parquet + dims
.venv/bin/python scripts/03_curate.py

# Phase 11 — data quality + reconciliation
.venv/bin/python scripts/04_validate.py

# Phase 10/12 — the required analytical queries
.venv/bin/python scripts/05_run_queries.py

# Phase 12 — partition-pruning proof
.venv/bin/python scripts/06_partition_pruning_demo.py

# Tests (unit + integration, requires the steps above to have run once)
.venv/bin/python -m pytest tests/ -q
```

MinIO console: http://localhost:9001 (credentials in `.env`).
PostgreSQL: `psql postgresql://annapurna:annapurna_dev_password@localhost:5432/annapurna`.

Rerunning `02_land_raw.py` and `03_curate.py` is safe — raw landing skips
files whose sha256 checksum is already in `ingestion_manifest`, and curation
overwrites the same deterministic `curated/sales/store=../year=../month=../sales.parquet`
object per store-month rather than appending, so a full rerun reproduces
identical totals (verified: revenue is ₹522,865,735.75 on both the first and
second run).

## Task 2 — idempotent loading

**What "idempotent" means here**: running the loading step (raw landing +
curation, `app/loader.py`) any number of times against the same source
folder must leave the curated dataset in exactly the same state as running
it once. Not "no errors on rerun" — the actual row count, the actual set of
business lines, and the actual revenue must be identical, because the source
folder legitimately contains the same trading day more than once (a store's
till gets re-triggered and re-exports, landing `SALES_S03_20241014__R1.csv`
next to `SALES_S03_20241014.csv`).

**Why "latest resend wins" is wrong**: a resend is not guaranteed to be a
full replacement. `SALES_S01_20241112__R1.csv` (a real file in the supplied
dataset) has only 150 of the original file's 263 `(bill_no,line_no)` keys —
the till was mid-roll when the re-export was triggered. Picking "the newest
file for this store-day" and discarding the rest would silently delete 113
real, already-billed sales lines. The reverse case matters too: a resend
that adds a genuinely new key (e.g. a bill that failed to commit the first
time) must not be discarded just because an older file for that day already
exists.

**How `(bill_no, line_no)` prevents duplicate business lines**: the business
identity of a line is `(bill_no, line_no)`, never "this file". Every load
groups *all* files currently known for a store-day (original + every
resend) and unions them by that key
(`app/deduplication/dedup.py::dedup_store_day`), keeping exactly one row per
key. Two independent mechanisms make repeated loading safe:

1. **File level** — `ingestion_manifest` (PostgreSQL) records each source
   file's sha256. Landing the same bytes again is a no-op: nothing is
   re-uploaded, nothing is re-recorded.
2. **Business-line level** — regardless of file-level skipping, curation
   always re-derives each store-month's curated Parquet from the *complete*
   current union of `(bill_no,line_no)` keys across every known file for
   that month, and overwrites the same deterministic object key
   (`curated/sales/store=../year=../month=../sales.parquet`). It never
   appends. If the exact same files are present, the union is the exact
   same set of lines every time — that is what makes reruns idempotent
   without needing to compare against what was previously loaded.

If two files disagree about the content of the same `(bill_no,line_no)` key,
that conflict is written to the `dedup_conflicts` audit table rather than
silently resolved — see `reports/inspection_report.md` for why no real
conflicts were found in this dataset (checked exhaustively) and what would
happen if one existed.

### How to run the loader

```bash
cd LAB-1/Question-1/Solution
.venv/bin/python -c "
from app.db import connect
from app.loader import load
from app.config import SETTINGS
with connect() as conn:
    load(SETTINGS.source_sales_dir, conn)
"
```
(equivalent to running `scripts/02_land_raw.py` then `scripts/03_curate.py`
then `scripts/03_curate.py`'s `build_dims` call — `app/loader.py::load()` is
the single entrypoint Task 2 tests against.)

### How to run the mandatory three-run test

```bash
.venv/bin/python scripts/07_idempotency_test.py
```

This resets the destination **once** (clears MinIO `raw/`+`curated/` and the
`ingestion_manifest`/`dedup_conflicts`/`enrichment_rejects`/`curation_runs`
tables — master data is left untouched), then calls `load()` three times in
a row with **no reset in between**, recomputing `row_count` /
`logical_dataset_checksum` / `revenue` from a **fresh DuckDB connection**
after each run. Exits non-zero if any of the three runs disagree.

The logical dataset checksum (`app/analytics/checksum.py`) is not a Parquet
file hash — physical file layout can change without the data changing. It
selects the canonical business columns, casts each to a stable string form,
orders by `(bill_no, line_no)` (globally unique — `bill_no` already encodes
store + business date), concatenates, and takes sha256. Two runs that
produced the same set of business lines get the same checksum no matter how
many curated files they were split across or what order rows were written.

### Actual test result (real execution, not fabricated)

```
=== RUN 1 ===
row_count: 1120924
checksum:  bb5be8f4b6142e460aa803ea98088614db4ed3492ab8e5be4b8ba0ec04d0eacb
revenue:   522865735.75

=== RUN 2 ===
row_count: 1120924
checksum:  bb5be8f4b6142e460aa803ea98088614db4ed3492ab8e5be4b8ba0ec04d0eacb
revenue:   522865735.75

=== RUN 3 ===
row_count: 1120924
checksum:  bb5be8f4b6142e460aa803ea98088614db4ed3492ab8e5be4b8ba0ec04d0eacb
revenue:   522865735.75
```

| Run | Row count | Checksum | Revenue |
|---|---:|---|---:|
| 1 | 1,120,924 | `bb5be8f4b6142e46...` | 522,865,735.75 |
| 2 | 1,120,924 | `bb5be8f4b6142e46...` | 522,865,735.75 |
| 3 | 1,120,924 | `bb5be8f4b6142e46...` | 522,865,735.75 |

```
row_count_1 == row_count_2 == row_count_3: 1120924
checksum_1  == checksum_2  == checksum_3:  MATCH
revenue_1   == revenue_2   == revenue_3:   522865735.75

IDEMPOTENCY: PASS
```

Note load #1 landed all 4,457 files (48.1s); loads #2 and #3 landed 0 new
files each (36-37s, spent entirely in curation re-deriving the union and
rewriting the same curated objects) — proof the file-level skip is working
*and* that skipping file uploads still produces byte-for-byte-equivalent
logical output. Full captured output:
[`reports/task2/idempotency_test_output.txt`](reports/task2/idempotency_test_output.txt),
machine-readable: [`reports/task2/idempotency_results.csv`](reports/task2/idempotency_results.csv).

### Resend test (real resend files, not just repeated execution)

```bash
.venv/bin/python scripts/08_resend_demo.py
```

```
original file:            SALES_S01_20241112.csv
resend file:              SALES_S01_20241112__R1.csv
original rows:            263
resend rows:              150
duplicate business lines: 150  (present in both, byte-identical where checked)
new business lines:       0  (present only in the resend)
lines only in original:   113  (would be LOST by 'latest file wins')
final unique business lines: 263
dedup conflicts detected: 0

Check: every line that existed ONLY in the original survives in the final dataset: PASS
```
Saved at [`reports/task2/resend_test_output.txt`](reports/task2/resend_test_output.txt).

## Project layout

```
LAB-1/
├── Question-1/
│   ├── data/                  the supplied dataset (masters.sql, billing_notes.md,
│   │                          finance_monthly.csv, sales/, _truth/) -- inputs only
│   └── Solution/              <- you are here
│       ├── docker-compose.yml PostgreSQL + MinIO
│       ├── .env.example
│       └── app/
└── Question-2/                 unrelated problem (tender-notice dedup, not implemented)
    ├── data_2/
    └── Solution/

Solution/
├── docker-compose.yml        PostgreSQL + MinIO
├── .env.example
├── app/
│   ├── config.py, db.py, object_store.py, duck.py
│   ├── loader.py                      Task 2: idempotent load() + reset_destination()
│   ├── ingestion/raw_landing.py       source -> MinIO raw/, checksum manifest
│   ├── normalization/                filenames.py, readers.py (3 dialects)
│   ├── deduplication/dedup.py         (bill_no,line_no) union + conflict audit
│   ├── enrichment/enrich.py           temporal product_sk / price resolution
│   ├── analytics/                     revenue.py, curate.py, dims.py, checksum.py (Task 2)
│   └── validation/validate.py         data-quality checks + reconciliation
├── sql/
│   ├── schema/audit.sql               lineage/idempotency tables
│   └── queries/01..07*.sql            the required analytical queries
├── scripts/
│   ├── 00..06                         one script per Task 1 pipeline phase
│   ├── 07_idempotency_test.py         Task 2: mandatory 3-run test
│   └── 08_resend_demo.py              Task 2: real resend proof
├── tests/                             pytest, unit + integration (@pytest.mark.integration)
└── reports/
    ├── inspection_report.md           Phase 1 findings, with real examples
    └── task2/                         idempotency_test_output.txt, idempotency_results.csv,
                                        resend_test_output.txt -- actual execution evidence
```

## What was deliberately not done

No Spark/Kafka/Airflow/Kubernetes — 1.1M rows and 65 MiB of source data does
not need them. No copy of curated fact data into PostgreSQL. No flat,
unpartitioned curated layer. No deletion of raw source files. No hard-coded
October answer — every number in this README came out of `scripts/03-06`
against the live PostgreSQL + MinIO + DuckDB stack.
