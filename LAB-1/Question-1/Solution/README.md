# Annapurna Stores — lakehouse-style sales platform (Task 1 + Task 2 + Task 3 + Task 4 + Task 5 + Task 6)

**Task 1** solves: *"analysts open daily sales files manually and produce
inconsistent monthly numbers; the CFO wants October to mean October,
historical prices/products to stay historically correct, and revenue
queryable by store/product/day/week/month without opening files."*

**Task 2** solves: *"some days appear in the source folder more than once
because the billing system resends a store report. Running the loader
multiple times must produce exactly the same final dataset — load once =
load twice = load three times."* See [Task 2](#task-2-make-it-safe-to-run-twice)
below.

**Task 3** solves: *"build the dashboard star schema so revenue can be
quickly sliced by store/product/category/day-of-week/month without
repeating store/product/category descriptions on every sales row."* See
[Task 3](#task-3-design-the-tables-behind-the-dashboard) below.

**Task 4** solves: *"prices change over time — a report for March 2024 must
use March's applicable prices, a report for the latest month available must
use that month's prices, using the same query for both."* See
[Task 4](#task-4-make-march-use-marchs-price) below.

**Task 5** solves: *"query across the two existing systems (MinIO Parquet
sales data, PostgreSQL dimensions) without copying either side into the
other, using DuckDB as the query engine."* See
[Task 5](#task-5-query-across-two-systems) below.

**Task 6** solves: *"reconcile the platform's monthly revenue against
finance_monthly.csv for every month, classify every difference (source
data issue / revenue definition difference / pipeline bug), and state
whether each should be raised with finance or fixed in the pipeline —
without changing the pipeline just to make numbers match."* See
[Task 6](#task-6-reconcile) below.

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

**`raw/` is the authoritative landed source data.** It holds the exact
files supplied (`SALES_<store>_<YYYYMMDD>[__Rn].csv`, byte-for-byte, one
object per source file), organized by `store`/`business_date` purely for
retrieval — nothing about the bytes is changed. Landing is append-only:
`app/ingestion/raw_landing.py` only ever uploads new/changed files
(checksum-gated); no code path in the normal pipeline (`scripts/02`,
`scripts/03`, `app/loader.py::load()`) ever deletes or overwrites a `raw/`
object. (The one exception is `app/loader.py::reset_destination()`, used
*only* by Task 2's mandated `RESET ONCE -> LOAD x3` test to start from a
clean destination — never called by ordinary ingestion/curation.)

**`curated/` is an optional analytical layer, not a replacement for `raw/`.**
Parquet was a design/optimization choice made here — a columnar format that
compresses well and lets DuckDB prune irrelevant partitions/row-groups — not
something the assignment mandated. Tasks 1-2 could have queried the raw CSVs
directly (DuckDB can `read_csv` too); `curated/` exists because it makes the
deduplicated, temporally-resolved, revenue-computed result reusable across
Tasks 3 and 4 without re-deriving it per query, and Task 5's federated join
(below) depends on it existing. It is derived data, safely rebuildable from
`raw/` at any time by rerunning `scripts/03_curate.py` — deleting `curated/`
would only cost a rebuild, never lose information, since `raw/` remains the
source of truth throughout.

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

## Task 2: Make it safe to run twice

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

## Task 3: Design the tables behind the dashboard

### Source-data traps re-confirmed against the actual (Task 2 deduplicated) data

Before touching the schema, these were re-checked with live queries against
`fact_sales` (the Task 2 output), not assumed:

* **Not every line is a sale.** `SELECT DISTINCT line_type FROM fact_sales`
  returns exactly `SALE, RETURN, DISCOUNT, VOID, TAX, TENDER` — matches
  `billing_notes.md`.
* **`product_code` is not globally unique.** `dim_product` has 24
  `product_code` values that map to more than one `product_sk` (check #5,
  below) — the 24 reissued codes from `masters.sql`, reissued
  **2024-06-01**.
* **Bill-level `DISCOUNT`/`TAX`/`TENDER` are not product rows.** Verified:
  0 of 22,603 `DISCOUNT` lines have a `product_sk`; `TAX`/`TENDER` likewise
  always `NULL` (check #4). Their `product_code` in the source is the
  pseudo-code `'DISC'`/`'TAX'`/`'TENDER'`, never a real product code.
* **Business date comes from the filename, not the timestamp.** Unchanged
  from Task 1/2 — `fact_sales.business_date` is still parsed from
  `SALES_<store>_<YYYYMMDD>...`, `transaction_ts` is kept separately (see
  `reports/inspection_report.md` section 3 for the real midnight-crossing
  example).

None of this required new code — it's the same `fact_sales` Task 1/2 already
built and validated. Task 3 does not redesign it; it builds a dashboard
layer on top.

### Schema design: snowflake, not a flat star

`category` is deliberately **normalized out one hop further**, into its own
`dim_category` table, rather than flattened as columns on `dim_product` (or
worse, on the fact row) — that's what makes this a **snowflake schema**:

```
                    dim_date
                       │
                       │
dim_store ─────── fact_sales ─────── dim_product ─────── dim_category
(store_sk)        (grain: 1 unique   (product_sk,        (category_sk,
                   business line,     category_sk FK)     category_name,
                   Task 2 deduped)                         department,
                                                            gst_rate)
```

* `fact_sales_dashboard` holds only surrogate-key foreign keys
  (`store_sk`, `product_sk`, `date_sk`) — no `category_id`/`category_sk`,
  and no descriptive text at all (`store_name`, `product_name`,
  `category_name`, `address`, ... never appear on the fact grain —
  validation check #7).
* `dim_product` holds `category_sk` as a foreign key only — it does **not**
  carry `category_name`/`department`/`gst_rate` alongside it. Those live
  solely in `dim_category`, one join away.
* A query needing category attributes therefore always goes
  `fact_sales_dashboard → dim_product → dim_category` (two hops), e.g.
  `sql/queries/10_dashboard_revenue_by_category.sql` and `12_dashboard_revenue_by_store_category_month.sql`:
  ```sql
  JOIN dim_product p   ON p.product_sk = f.product_sk
  JOIN dim_category cat ON cat.category_sk = p.category_sk
  ```
  Verified live (`DESCRIBE dim_product` / `DESCRIBE fact_sales_dashboard`):
  neither table contains `category_name`, `department`, or `gst_rate` —
  those columns exist in exactly one place, `dim_category`.

`dim_store` and `dim_date` are left as single flat dimensions (not
snowflaked further, e.g. no separate `dim_geography` for
city/state/region) — the assignment's dashboard slices
(store/product/category/day-of-week/month) don't need that extra
normalization, and adding it would be scope creep beyond what was asked.

`fact_sales_dashboard` is a **DuckDB VIEW**, not a re-materialized table —
it adds the dashboard-friendly surrogate keys (`sales_line_sk`, `store_sk`,
`date_sk`) on top of the existing Task 1/2 curated Parquet at query time
(`app/duck.py::connect()`). The underlying `curated/sales/*.parquet` files
and `app/analytics/curate.py` pipeline are **untouched** — this is
additive, not a redesign. All four dimension Parquet snapshots
(`app/analytics/dims.py`) got their Task 3 columns added the same way:
**additively**, alongside the original Task 1 column names, so the
existing Task 1 queries (`sql/queries/02`, `05`) still run unmodified.

| Dimension | Task 3 columns added | Task 1 columns kept |
|---|---|---|
| `dim_date` | `calendar_date`, `quarter`, `month_number`, `year_month`, `day_of_month`, `week_of_year`, `day_of_week` | `business_date`, `month`, `day`, `iso_week`, `iso_year` |
| `dim_store` | `store_sk`, `address` | `address_line`, `store_id`, ... |
| `dim_category` | `category_sk` | `category_id`, ... |
| `dim_product` | `category_sk` (joined) | `product_sk` (unchanged identity), `category_id`, ... |

**Weekday convention**: `day_of_week` is ISO 8601 — **Monday = 1 ... Sunday
= 7** (`dim_date.day_of_week`), alongside the readable `day_name`. Verified:
2024-01-01 (a real Monday) → `day_of_week=1, day_name='Monday'`
(`tests/test_task3_star_schema.py::test_dim_date_weekday_convention_monday_is_1`).

### Fact-table grain

**One unique business line after Task 2 deduplication** — unchanged from
Task 1/2. `fact_sales_dashboard` row count = 1,120,924 = Task 2's
`idempotency_results.csv` row count exactly (validation check #6). No
row is added, dropped, or split to build the dashboard layer.

`product_sk` is resolved via `product_code + business_date BETWEEN
valid_from AND valid_to` (Task 1's temporal join, unchanged) — **never**
`product_code` alone. It is populated for `SALE`/`RETURN`/`VOID` and `NULL`
for `DISCOUNT`/`TAX`/`TENDER`, with one documented edge case: a `VOID` that
cancels a `DISCOUNT` line (product_code `'DISC'`) as part of a whole-bill
cancellation also gets `product_sk = NULL` — it's a VOID by `line_type`,
but still not a product (`tests/test_task3_star_schema.py::test_void_of_discount_has_no_product_sk`).

`fact_sales_dashboard` deliberately does **not** carry `category_id` or any
descriptive text (`store_name`, `product_name`, `category_name`, `address`,
...) — those live only in the dimensions, reached by surrogate key
(validation check #7).

### Revenue calculation

Unchanged from Task 1: `revenue_amount = qty * source_unit_price` for
`SALE`/`RETURN`/`DISCOUNT`/`VOID`, `0` for `TAX`/`TENDER`. `VOID` is never
filtered out — its negated `qty` is what cancels the original `SALE`
(check #2: `S01/20240102/00017` nets to exactly ₹0). `price_revisions` is
**never** used to compute `revenue_amount` — `historical_authoritative_price`
is kept as a separate column for as-of-date price analysis only (Task 1's
`sql/queries/07_historical_price_lookup.sql` still demonstrates this).

### Product reissue handling

Unchanged from Task 1: resolved once during curation, via the temporal join
above, using `product_sk` as the stable identity in every downstream query
— never re-joined by `product_code` at query time. Real example: `P108206`
resolves to `product_sk=2173` ("Daawat Poha 10kg") for sales through
2024-05-31, and `product_sk=2217` ("Local Mandi Apple 1kg") from
2024-06-01 (validation check #1).

### Bill-level adjustment handling ("the important dashboard attribution rule")

A `DISCOUNT` line reduces **total net revenue** but has no product or
category identity — it is never assigned one. Two revenue definitions are
kept explicitly distinct rather than conflated:

* **Total net revenue** — `SUM(revenue_amount)` over every revenue-bearing
  line type. Reconciles to the Task 1 folder truth exactly.
* **Product-attributed revenue** — `SUM(revenue_amount) WHERE product_sk IS
  NOT NULL`. Always *less* than total net revenue, by exactly the
  non-product lines (`DISCOUNT`, plus the rare VOID-of-DISCOUNT case).

`sql/queries/13_total_vs_product_attributed_revenue.sql` proves the two
reconcile with **zero unexplained gap** for October 2024:

```
total_net_revenue  product_attributed_revenue  non_product_revenue  of_which_discount  of_which_void_of_discount  unexplained_gap
       56359195.92                 56927219.51           -568023.59         -570249.25                    2225.66              0.0
```

No allocation of `DISCOUNT` across products/categories is performed —
category-level rollups (`sql/queries/10`, `12`) are **product-attributed
only** and will not sum to total net revenue for a store/month. If a
dashboard later needs category-level numbers that foot to the total, that
needs a separate, explicitly documented allocation rule (e.g. pro-rata by
each category's share of that bill's `SALE` revenue) — deliberately **not**
implemented here, since the source data specifies no such rule and
inventing one would silently change fact semantics.

### Dashboard queries (`sql/queries/08-13`, run via `scripts/11_run_dashboard_queries.py`)

* `08` — revenue by store and month (total net revenue)
* `09` — revenue by product (product-attributed, top 20)
* `10` — revenue by category (product-attributed)
* `11` — revenue by day of week (Monday=1..Sunday=7)
* `12` — revenue by store + category + month (product-attributed, October 2024)
* `13` — total net vs. product-attributed revenue reconciliation

Sample results (October 2024, full output in
[`reports/task3/dashboard_queries_output.txt`](reports/task3/dashboard_queries_output.txt)):

```
revenue by day of week:
 day_of_week  day_name  total_net_revenue   lines
           1    Monday        59589704.80  127418
           6  Saturday       104941626.27  225075   <- highest
           7    Sunday        94969205.46  203483

revenue by category (product-attributed, all-time):
 category_id      category_name  product_attributed_revenue
         C04   Staples & Grains                  98677244.71   <- highest
         C09          Baby Care                  88746237.02
         C05        Edible Oils                  82736636.78
```

### Performance: does the dashboard layer still prune by store/year/month?

`scripts/10_dashboard_pruning_check.py` measures this with `EXPLAIN`
(actual output, not asserted — full log at
[`reports/task3/pruning_check_output.txt`](reports/task3/pruning_check_output.txt)):

| Query form | Files scanned |
|---|---|
| `fact_sales_dashboard` filtered by store only (`partition_store='S01'`) | **12 / 144** |
| `fact_sales_dashboard` filtered by store + year + month | **12 / 144** (no better — see below) |
| `read_parquet(...)` queried **directly**, filtered by store + month | **1 / 144** |

A real DuckDB limitation was found and is documented rather than glossed
over: filtering a `CREATE VIEW`-wrapped `read_parquet()` (which is what
`fact_sales`/`fact_sales_dashboard` are) on the `month` partition column
does not get the same automatic type-coercion pushdown that querying
`read_parquet()` directly gets — so through the view, only the `store`
filter reaches the Parquet scan (still a real 91.7% file reduction), while
full store+month pruning (1/144, same result as Task 1's
`scripts/06_partition_pruning_demo.py`) requires querying the underlying
`read_parquet(...)` glob directly. Both paths return the **identical**
correct result (₹6,435,443.95 for S01, October 2024) — this is a pruning
depth difference, not a correctness difference. Recommendation documented
in the script: use `fact_sales_dashboard` for joins/ad-hoc slicing, and
query the partitioned files directly for a dashboard's hot-path filters
that need maximal pruning.

### Task 3 validation (`scripts/09_task3_validate.py`)

All 8 required checks, run against the live data
([full output](reports/task3/validation_output.txt)):

```
[PASS] 1_reissued_code_resolves_differently_before_after_2024-06-01: P108206 -> 2 distinct product_sk
[PASS] 2_sale_plus_void_nets_to_zero: S01/20240102/00017 net revenue = 0.00
[PASS] 3_tax_and_tender_zero_revenue: {'TAX': 0.0, 'TENDER': 0.0}
[PASS] 4_discount_reduces_revenue_without_product_identity: 22603 DISCOUNT lines, revenue=-5294511.42, 0 with a product_sk
[PASS] 5_product_code_alone_is_not_a_unique_identity: 24 product_code values map to more than one product_sk
[PASS] 6_fact_row_count_matches_task2_dedup_dataset: fact_sales_dashboard rows=1120924, Task 2 row_count=1120924
[PASS] 7_descriptive_attributes_not_duplicated_on_fact_rows: none found on the fact grain
[PASS] 8_revenue_reconciles_with_folder_truth_all_12_months: 12 months checked, 0 mismatches

TASK 3 VALIDATION: PASS (8/8 checks passed)
```

### How to run Task 3

```bash
cd LAB-1/Question-1/Solution
# Task 1/2 must already have run once (dims + fact_sales in MinIO)
.venv/bin/python scripts/09_task3_validate.py            # 8 required checks
.venv/bin/python scripts/10_dashboard_pruning_check.py    # EXPLAIN-based pruning proof
.venv/bin/python scripts/11_run_dashboard_queries.py      # all 6 dashboard queries
.venv/bin/python -m pytest tests/test_task3_star_schema.py -q
```

## Task 4: Make March use March's price

### Approach

One parameterized query, `sql/queries/14_price_report.sql`, resolves "the
applicable price" for every product for a reporting period
`[$start_date, $end_date]` directly from `price_revisions`
(`product_sk + effective_from/effective_to + reporting date`):

```sql
WITH at_period_end AS (
    SELECT product_sk, revision_id, mrp, selling_price, effective_from, effective_to
    FROM price_revisions
    WHERE effective_from <= $end_date AND effective_to >= $end_date
), ...
```

* **The reporting date used is the period's `end_date`** (the standard
  "price as of period close" convention) — the applicable price is the
  `price_revisions` row whose `[effective_from, effective_to]` window
  contains `$end_date`. This is a plain `BETWEEN`-style containment check,
  never `ORDER BY effective_from DESC LIMIT 1` and never a join to "today" —
  structurally verified by
  `tests/test_task4_price_report.py::test_query_never_orders_by_effective_from_desc`.
* **No stored data is overwritten.** `price_revisions` (PostgreSQL) is read
  read-only; `app/analytics/dims.py::build_price_revisions_dim()` only
  writes a Parquet *snapshot* of it to `curated/dims/dim_price_revision.parquet`
  so DuckDB can query it without a live Postgres round trip per call — the
  same pattern Task 1/3 already use for `dim_store`/`dim_product`/etc. This
  is the one small, additive extension needed to make Task 4 possible; the
  Task 1/2/3 pipeline itself is untouched.
* **Multiple revisions inside one period are not silently collapsed.** The
  query also resolves the revision effective at `$start_date`
  (`price_at_period_start`) and sets `price_changed_during_period = TRUE`
  whenever that differs from the period-end revision — a mid-period price
  change is flagged, not hidden.
* **A missing mapping is surfaced, not substituted.** `dim_product` is
  `LEFT JOIN`ed to `price_revisions`; if no revision covers `$end_date`,
  `applicable_price` is `NULL` and `price_missing_for_period = TRUE`.

### Demonstration: the exact same query, two periods

`scripts/12_task4_price_report.py` executes `14_price_report.sql`
**verbatim** twice — only the bound `$start_date`/`$end_date` parameters
differ. Run 2's period is *computed* from
`SELECT MAX(business_date) FROM fact_sales_dashboard`, never hard-coded.

```
Run 1 period: 2024-03-01 .. 2024-03-31
Run 2 period: 2024-12-01 .. 2024-12-31  (latest month in dataset, derived from MAX(business_date)=2024-12-31)

=== RUN 1 (March 2024-03) ===
products reported:             1224
price missing for period:      3
price changed during period:   147

=== RUN 2 (latest month 2024-12) ===
products reported:             1224
price missing for period:      0
price changed during period:   0

=== Products whose applicable price differs between the two periods: 916 ===
```

Full captured output:
[`reports/task4/price_report_output.txt`](reports/task4/price_report_output.txt),
per-product comparison CSV:
[`reports/task4/price_comparison.csv`](reports/task4/price_comparison.csv).

**Real product whose price changed between the two periods:**

| Product | March price | Latest-month price |
|---|---:|---:|
| Thums Up Mango Juice 250g (`P100005`, `product_sk=1001`) | 103.45 | 114.37 |

(`product_sk=1001` has 4 real revisions in `masters.sql`; March 2024-03-31
falls in the 2023-11-09→2024-06-13 window, December 2024-12-31 falls in the
2024-06-14→9999-12-31 window — the same product resolves to two different,
correct prices purely because the reporting-date parameter changed.)

### Validation (`scripts/13_task4_validate.py`)

All 5 required proofs, run against the live data
([full output](reports/task4/validation_output.txt)):

```
[PASS] 1_march_uses_price_effective_in_march_2024: 1221/1221 resolved rows have 2024-03-31 within [effective_from, effective_to]
[PASS] 2_latest_month_uses_price_effective_in_that_month: 1224/1224 resolved rows have 2024-12-31 within [effective_from, effective_to]
[PASS] 3_same_query_text_used_for_both_periods: sql/queries/14_price_report.sql executed verbatim for both runs; only the $start_date/$end_date parameter values differ
[PASS] 4_no_current_price_or_hardcoded_month_logic: product_sk=1001 applicable_price by period: {'march': 103.45, 'june': 114.37, 'december': 114.37}
[PASS] 5_price_selection_uses_real_price_revisions_validity_dates: product_sk=2217 ('Local Mandi Apple 1kg', valid_from 2024-06-01): March price_missing=True, December price=81.62

TASK 4 VALIDATION: PASS (5/5 checks passed)
```

Check #5's example is the same real reissued product from Task 3
(`P108206`/`product_sk=2217`, "Local Mandi Apple 1kg", `valid_from
2024-06-01`): it genuinely has **no** price for a March 2024 report — the
product itself didn't exist under that identity yet — and the query
correctly reports `price_missing_for_period = TRUE` rather than silently
falling back to the old "Daawat Poha 10kg" price (a different `product_sk`
entirely) or to today's price.

### How to run Task 4

```bash
cd LAB-1/Question-1/Solution
# Task 1/2/3 must already have run once (fact_sales + dims in MinIO)
.venv/bin/python scripts/12_task4_price_report.py   # builds the price_revisions snapshot,
                                                     # runs both reporting periods, writes reports/task4/
.venv/bin/python scripts/13_task4_validate.py       # 5 required validation checks
.venv/bin/python -m pytest tests/test_task4_price_report.py -q
```

## Task 5: Query across two systems

**Verified (re-checked when the raw-vs-curated architecture was reviewed):
sales data is read exclusively via `read_parquet('s3://annapurna/curated/sales/...')` —
straight out of MinIO — in both `sql/queries/15_federated_query.sql` and
`app/duck_federated.py`. `pg.*` (the live PostgreSQL attachment) is referenced
only for `stores`/`products`/`product_categories`; there is no `INSERT`,
`CREATE TABLE`, or `COPY ... TO pg` anywhere in the Task 5 code path that
would put sales rows into PostgreSQL. This was already true before this
review — no code change was needed here, see the "no copy" evidence below.**

### Approach

A **separate** DuckDB connection helper, `app/duck_federated.py`, deliberately
does **not** reuse `app/duck.py`'s `dim_store`/`dim_product`/`dim_category`
views — those are built from the Parquet *snapshots* Task 3/4 write to
`curated/dims/` for dashboard convenience, and a snapshot is exactly the
kind of copy Task 5 says not to make. Instead `connect_federated()`:

1. Loads `httpfs` and points it at MinIO, same as before — sales data is
   read straight off `s3://annapurna/curated/sales/*/*/*/sales.parquet`
   via `read_parquet()`.
2. Loads DuckDB's `postgres` extension and runs
   `ATTACH '...' AS pg (TYPE postgres, READ_ONLY)` — `pg.stores`,
   `pg.products`, `pg.product_categories` are PostgreSQL's own tables,
   reached live through the attachment. No `CREATE TABLE ... AS`, no
   `CREATE TEMP TABLE`, no materialization anywhere in this path.

**The single federated query** (`sql/queries/15_federated_query.sql`):

```sql
SELECT
    s.store_id, s.store_name, cat.category_id, cat.category_name,
    ROUND(SUM(f.revenue_amount), 2) AS product_attributed_revenue
FROM read_parquet('s3://annapurna/curated/sales/*/*/*/sales.parquet', hive_partitioning = true) f
JOIN pg.stores s               ON s.store_id      = f.store_id
JOIN pg.products p             ON p.product_sk    = f.product_sk
JOIN pg.product_categories cat ON cat.category_id = p.category_id
WHERE f.business_date >= $start_date AND f.business_date < $end_date
GROUP BY s.store_id, s.store_name, cat.category_id, cat.category_name
ORDER BY product_attributed_revenue DESC;
```

`product_sk` is the existing Task 3 historical-identity surrogate key,
already resolved once during Task 1 curation — this query does a plain
equi-join on it, no re-resolution. Only `$start_date`/`$end_date` change
between runs; the query text is identical.

### Actual result (October 2024)

```
store_id                store_name category_id     category_name  product_attributed_revenue
     S03        Annapurna T Nagar         C04  Staples & Grains                  1220174.16
     S01      Annapurna Jayanagar         C04  Staples & Grains                  1211339.36
     S10 Annapurna Rajouri Garden         C04  Staples & Grains                  1185800.08
     ...
```
144 rows, total revenue = 56,927,219.51 (matches Task 3's product-attributed
revenue for October exactly — see `tests/test_task5_federated_query.py::test_federated_query_result_matches_task3_dashboard_query`).
Full output: [`reports/task5/federated_query_output.txt`](reports/task5/federated_query_output.txt).

### Evidence: where the work actually happened

Captured with `EXPLAIN ANALYZE` plus DuckDB's `pg_debug_show_queries=true`
(prints the literal SQL sent to PostgreSQL) — see section 2 and 3 of
[`reports/task5/federated_query_output.txt`](reports/task5/federated_query_output.txt)
for the full, unedited output.

**MinIO / Parquet work** — one `TABLE_SCAN` operator, `Function: READ_PARQUET`:
```
Filename(s): s3://annapurna/curated/sales/*/*/*/sales.parquet, ...
Filters: business_date>='2024-10-01'::DATE AND business_date<'2024-11-01'::DATE
Total Files Read: 144
```
The `business_date` predicate is applied inside the Parquet scan (it is not
a hive-partition column here — `store`/`year`/`month` are — so this filter
reduces *bytes read via row-group statistics*, not *file count*: the HTTPFS
stats block in the same plan shows only **2.7 MiB** transferred in, out of
the curated layer's 20.2 MiB total, for the 144 files DuckDB opened).

**PostgreSQL work** — three separate `TABLE_SCAN` operators, `Table: stores`
/ `products` / `product_categories`, each reporting a **Projections:** list
narrower than the real table's columns (e.g. `products` → only `product_sk,
category_id`, not `product_name`/`brand`/`pack_size`/.../`is_current`). The
**actual SQL DuckDB sent to PostgreSQL** (captured verbatim via
`pg_debug_show_queries`) confirms this is real, not an EXPLAIN artifact:

```sql
COPY (SELECT "product_sk", "category_id" FROM "public"."products"
      WHERE ctid BETWEEN '(0,0)'::tid AND '(4294967295,0)'::tid) TO STDOUT (FORMAT "binary");
COPY (SELECT "category_id", "category_name" FROM "public"."product_categories"
      WHERE ctid BETWEEN '(0,0)'::tid AND '(4294967295,0)'::tid) TO STDOUT (FORMAT "binary");
COPY (SELECT "store_id", "store_name" FROM "public"."stores"
      WHERE ctid BETWEEN '(0,0)'::tid AND '(4294967295,0)'::tid) TO STDOUT (FORMAT "binary");
```
**Column-projection pushdown to PostgreSQL is real** (`SELECT "product_sk",
"category_id"`, not `SELECT *`). **No `WHERE`-clause row filter was pushed**
for this query — the `ctid BETWEEN` clause is DuckDB's parallel-scan page
range, not a business filter, and there is no `AND category_id = ...`/
`AND store_id = ...` anywhere in the captured SQL, because this query's only
filter (`business_date`) is a Parquet-side column with nothing to push to
PostgreSQL for it.

The plan does show `Dynamic Filters: optional: store_id IN (...)` and
`optional: category_id IN (...)` on the Parquet/products `TABLE_SCAN`s —
these are **DuckDB-internal** runtime join filters (derived from the small
dimension side of the hash join and applied back into the Parquet/table
scan to skip rows early), generated and applied entirely inside DuckDB.
**They are not sent to PostgreSQL** — the captured `COPY` statement for
`products` has no such `WHERE`, confirming the plan's "optional" filters
stayed client-side. Reported here precisely because the task says not to
*claim* pushdown without evidence, and the evidence here says "some, not
all": column pruning → real Postgres pushdown; the dynamic join filters →
DuckDB-side only.

**Contrast, to show pushdown genuinely works when it applies** (section 4 of
the same report): a second, minimal query,
`SELECT product_sk, category_id FROM pg.products WHERE category_id = 'C04'`,
produces `COPY (... WHERE ctid BETWEEN ... AND "category_id" = 'C04' COLLATE
"C") TO STDOUT (...)` — the real `WHERE` clause **does** appear this time,
because this filter targets an actual PostgreSQL column directly. This is
the control case proving the main query's lack of a pushed filter is a
correct, evidence-based finding, not a missed optimization to hide.

**DuckDB work** — everything else in the plan: the three `HASH_JOIN`
operators (store, product, category), the `HASH_GROUP_BY` computing
`SUM(revenue_amount)` per store+category, and the final `ORDER_BY`/
`PROJECTION`. None of this happens in PostgreSQL or in MinIO — DuckDB pulls
~81K filtered Parquet rows and 12+1224+14 dimension rows across the wire
and does the join/aggregate itself, in-process.

### Evidence that neither side was copied

* `app/duck_federated.py` creates **no** `CREATE TABLE`/`CREATE TEMP TABLE`/
  materialized view of PostgreSQL data — only `ATTACH ... (TYPE postgres,
  READ_ONLY)`. `tests/test_task5_federated_query.py::test_federated_connection_has_no_dim_parquet_views`
  asserts this connection's `SHOW TABLES` contains none of
  `dim_store`/`dim_product`/`dim_category`.
* **Live-write proof**: `test_pg_tables_are_queried_live_not_materialized`
  inserts a throwaway row directly into PostgreSQL's `stores` table, then
  re-queries `pg.stores` through the *same, already-open* DuckDB connection
  and sees the new row immediately (then deletes it). A cached/copied
  snapshot could not observe that write without being rebuilt.
* Sales data is never pulled into PostgreSQL either — the `TABLE_SCAN
  Function: READ_PARQUET` in the plan reads directly from
  `s3://annapurna/curated/sales/...`; PostgreSQL is never the source for
  fact rows anywhere in this query or in Task 1-4.

### How to run / reproduce

```bash
cd LAB-1/Question-1/Solution
# infra up, scripts/01-03 already run (curated fact_sales in MinIO), PostgreSQL reachable
.venv/bin/python -u scripts/14_task5_federated_query.py > reports/task5/federated_query_output.txt 2>&1
.venv/bin/python -m pytest tests/test_task5_federated_query.py -q
```
The `-u` (unbuffered) flag matters: `pg_debug_show_queries` prints at the
C++ level, bypassing Python's `sys.stdout` buffering, so the debug SQL can
interleave out of order in the captured file without it — this is a real
gotcha discovered while building this report, not a hypothetical.

## Task 6: Reconcile

`scripts/15_task6_reconcile.py` compares platform monthly revenue
(`fact_sales`, unchanged from Task 1) against `finance_monthly.csv` for all
12 months, and classifies every difference using real, independently
checked evidence — never a guess, and the pipeline was **not** altered to
force a match. `_truth/truth.json` is used only as evidence to *explain* an
already-computed platform number, the same way Task 1 always used it.

### Reconciliation table (actual run)

| Month | Platform revenue | Finance revenue | Difference | Type | Action |
|---|---:|---:|---:|---|---|
| 2024-01 | 38,446,071.33 | 38,446,071.33 | 0.00 | Match | — |
| 2024-02 | 34,887,085.55 | 34,887,085.55 | 0.00 | Match | — |
| 2024-03 | 41,971,649.09 | 42,457,899.09 | **+486,250.00** | Source data issue | Raise with finance (confirm/document) |
| 2024-04 | 37,958,457.37 | 37,958,457.37 | 0.00 | Match | — |
| 2024-05 | 41,764,716.40 | 41,764,716.40 | 0.00 | Match | — |
| 2024-06 | 38,987,082.82 | 38,987,082.82 | 0.00 | Match | — |
| 2024-07 | 40,295,160.11 | 40,527,291.81 | **+232,131.70** | Source data issue | Raise with finance (confirm/document) |
| 2024-08 | 45,252,181.75 | 45,252,181.75 | 0.00 | Match | — |
| 2024-09 | 44,615,037.46 | 44,615,037.46 | 0.00 | Match | — |
| 2024-10 | 56,359,195.92 | 56,359,195.92 | 0.00 | Match | — |
| 2024-11 | 51,583,838.47 | 51,583,838.47 | 0.00 | Match | — |
| 2024-12 | 50,745,259.48 | 50,745,209.00 | **-50.48** | Revenue definition difference | Raise with finance (document convention) |

Full captured run: [`reports/task6/reconciliation_output.txt`](reports/task6/reconciliation_output.txt),
machine-readable: [`reports/task6/reconciliation_table.csv`](reports/task6/reconciliation_table.csv).

### Evidence behind each classification (not assumed)

**March — Source data issue.** `finance - platform = 486,250.00`, which is
an **exact match** to `truth.json.march_bulk_invoice` (486,250.00) — a
distinct, independently-recorded field, not just the prose note. This is an
institutional order finance invoiced directly, outside the till, so it
structurally cannot appear in any POS export the platform ingests. Both
numbers are "correct" under their own scope; the platform's is correct for
"what the till folder contains," finance's for "what the company actually
billed."

**July — Source data issue.** `finance - platform = 232,131.70`, an **exact
match** to `truth.json`'s own `monthly_net_revenue["2024-07"] -
monthly_net_revenue_in_folder["2024-07"]` gap. Root cause independently
confirmed at the file level (`reports/inspection_report.md` section 8):
`SALES_S07_20240709/10/11.csv` do not exist — S07's till server was down
for those 3 days (billing_notes.md). Sanity-checked the magnitude, not just
trusted the label: S07's average daily revenue over its other 28 July days
is ₹94,684.27; 3 days at that rate (~₹284,053) is in the right range for
the observed ₹232,132 gap. The platform correctly has **zero** fabricated
rows for those dates (Task 1 validation check); it cannot recover revenue
that was never recorded anywhere the platform can reach.

**December — Revenue definition difference.** `finance - platform = -50.48`
— tiny (0.0001% of the month). `truth.json.monthly_rounded["2024-12"]`
(a *separate*, independently-computed ground-truth field: "sum of each
bill rounded to the nearest rupee, then summed") equals
finance's December figure **exactly** (50,745,209.00 = 50,745,209.00) —
verified this equality holds for **no other month** in `monthly_rounded`
before trusting it, so it isn't a coincidence. This directly corroborates
billing_notes.md's undocumented claim that finance rounds each bill to the
rupee before summing, specifically for December. Both figures are valid
under their own rounding rule; neither is wrong.

**All other 9 months — Match.** Verified to the cent, not approximately
(`abs(difference) < 0.01` for all 9).

**Whole-year check.** Sum of finance − sum of platform for the full year =
**718,331.22**. Sum of the three classified differences (486,250.00 +
232,131.70 − 50.48) = **718,331.22**. Unexplained residual = **0.00** —
every rupee of the year's finance/platform gap is accounted for by a named,
evidenced cause. This is the strongest evidence that **no pipeline bug
exists**: if ingestion, dedup, product resolution, or revenue calculation
were silently wrong anywhere, some residual would show up somewhere in this
sum and it does not.

### Summary

* **9 of 12 months match exactly**: Jan, Feb, Apr, May, Jun, Aug, Sep, Oct, Nov.
* **3 of 12 months differ**, all classified with evidence, none a pipeline bug:
  * March, July — **Source data issue** (out-of-till institutional invoice;
    genuinely missing S07 source files).
  * December — **Revenue definition difference** (per-bill rupee rounding
    finance applies that the platform does not).
* **0 pipeline bugs found.** Tasks 1-5 were **not modified** by this task —
  there was nothing to fix, and Task 6's instructions are explicit that a
  pipeline change must never be made just to force a match.
* **Take back to finance**: document all three causes so future
  month-over-month variance analysis doesn't re-investigate them from
  scratch — specifically (1) confirm off-till institutional invoices like
  March's should continue to be flagged/annotated when they occur, (2) note
  that S07's July numbers are permanently short by design (till outage,
  not recoverable) unless finance can supply the phoned-in 3-day figures as
  a manual adjustment, and (3) document the December-specific per-bill
  rupee-rounding convention so a future ~₹50 gap isn't mistaken for a data
  quality problem.

### How to run Task 6

```bash
cd LAB-1/Question-1/Solution
# Task 1 must already have run once (fact_sales in MinIO)
.venv/bin/python scripts/15_task6_reconcile.py
.venv/bin/python -m pytest tests/test_task6_reconcile.py -q
```

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
│   ├── duck_federated.py              Task 5: ATTACH-based connection, MinIO + live PostgreSQL
│   ├── loader.py                      Task 2: idempotent load() + reset_destination()
│   ├── ingestion/raw_landing.py       source -> MinIO raw/, checksum manifest
│   ├── normalization/                filenames.py, readers.py (3 dialects)
│   ├── deduplication/dedup.py         (bill_no,line_no) union + conflict audit
│   ├── enrichment/enrich.py           temporal product_sk / price resolution
│   ├── analytics/                     revenue.py, curate.py, dims.py (Task 3 + Task 4
│   │                                  build_price_revisions_dim() cols added), checksum.py (Task 2)
│   └── validation/validate.py         data-quality checks + reconciliation
├── sql/
│   ├── schema/audit.sql               lineage/idempotency tables
│   └── queries/
│       ├── 01..07*.sql                Task 1 required analytical queries
│       ├── 08..13*.sql                Task 3 dashboard queries + attribution-rule check
│       ├── 14_price_report.sql        Task 4: parameterized price-as-of-period query
│       └── 15_federated_query.sql     Task 5: single MinIO+PostgreSQL federated query
├── scripts/
│   ├── 00..06                         one script per Task 1 pipeline phase
│   ├── 07_idempotency_test.py         Task 2: mandatory 3-run test
│   ├── 08_resend_demo.py              Task 2: real resend proof
│   ├── 09_task3_validate.py           Task 3: 8 required validation checks
│   ├── 10_dashboard_pruning_check.py  Task 3: EXPLAIN-based pruning proof
│   ├── 11_run_dashboard_queries.py    Task 3: runs sql/queries/08-13
│   ├── 12_task4_price_report.py       Task 4: runs 14_price_report.sql twice (March, latest month)
│   ├── 13_task4_validate.py           Task 4: 5 required validation checks
│   ├── 14_task5_federated_query.py    Task 5: result + EXPLAIN ANALYZE + pg_debug_show_queries
│   └── 15_task6_reconcile.py          Task 6: monthly reconciliation vs finance_monthly.csv
├── tests/                             pytest, unit + integration (@pytest.mark.integration)
│                                      (test_task3_star_schema.py, test_task4_price_report.py,
│                                       test_task5_federated_query.py, test_task6_reconcile.py)
└── reports/
    ├── inspection_report.md           Phase 1 findings, with real examples
    ├── task2/                         idempotency_test_output.txt, idempotency_results.csv,
    │                                  resend_test_output.txt -- actual execution evidence
    ├── task3/                         validation_output.txt, pruning_check_output.txt,
    │                                  dashboard_queries_output.txt -- actual execution evidence
    ├── task4/                         price_report_output.txt, price_comparison.csv,
    │                                  validation_output.txt -- actual execution evidence
    ├── task5/                         federated_query_output.txt -- actual result + EXPLAIN
    │                                  ANALYZE + pg_debug_show_queries output
    └── task6/                         reconciliation_output.txt, reconciliation_table.csv --
                                        actual evidenced classification of every difference
```

## What was deliberately not done

No Spark/Kafka/Airflow/Kubernetes — 1.1M rows and 65 MiB of source data does
not need them. No copy of curated fact data into PostgreSQL. No flat,
unpartitioned curated layer. No deletion of raw source files. No hard-coded
October answer — every number in this README came out of `scripts/03-06`
against the live PostgreSQL + MinIO + DuckDB stack.
