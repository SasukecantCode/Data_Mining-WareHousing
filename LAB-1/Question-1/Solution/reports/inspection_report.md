# Task 1 — Dataset Inspection Report

All numbers below were measured directly from the files in `Question-1/data/`
(paths in this report are relative to `LAB-1/`). Where the vendor's
`_truth/truth.json` states an expected count, the measured value is compared
against it — `_truth` is used only to *check* these measurements, never as an
input to the pipeline itself.

## 1. Source files

| Metric | Measured | truth.json |
|---|---|---|
| Sales CSV files | 4,457 | 4,457 (`files`) |
| Resend files (`__R1`/`__R2`) | 68 (54 × `__R1`, 14 × `__R2`) | — |
| Parquet files present | 0 | — (vendor notes mention Parquet as a historical possibility; none exist in this drop) |
| Total raw bytes (sales/) | 68,706,877 (~65.5 MiB) | — |
| Total raw data lines (all files incl. resend copies, excl. headers) | 1,137,585 | 1,137,585 (`raw_lines`) |
| Stores | 12 (S01–S12), 367–378 files each | 12 |
| Products (masters.sql) | 1,224 | 1,224 (`n_products`) |
| Price revisions (masters.sql) | 4,320 | 4,320 (`n_price_revisions`) |
| Reissued product codes (product_code appearing on >1 products row) | 24 | 24 (`n_reissued_codes`) |

Command used for the file/byte/line counts: `find`, `wc -l`, `printf '%s\n' | awk`
over `Question-1/data/sales`, excluding the `:Zone.Identifier` NTFS artifact files
that ship alongside every real file in this checkout (Windows download
metadata — not part of the dataset, filtered out everywhere).

## 2. File dialects (confirmed by sampling one file per store)

| Stores | Delimiter | Header | Timestamp format | Notes |
|---|---|---|---|---|
| S01–S05 | `,` | `bill_no,line_no,product_code,qty,unit_price,line_type,ts` | ISO `2024-01-01T14:19:31` | plain UTF-8 |
| S06–S09 | `;` | `bill_no;line_no;item_code;quantity;rate;type;txn_time` | `dd-mm-yyyy HH:MM:SS` e.g. `01-01-2024 17:29:57` | column names differ, need renaming |
| S10–S12 | `,` | `ts,bill_no,line_no,line_type,product_code,unit_price,qty` | epoch seconds UTC, e.g. `1704133242` | **UTF-8 BOM** at file start (`﻿` before `ts`); column *order* is not what the vendor notes describe (`ts` first, not last) — confirms the reader must select columns **by name after BOM-stripping**, never by fixed position |

No Parquet files exist in the current drop, so the reader abstraction supports
both formats but only the CSV path is exercised against real data. Forcing a
Parquet example would be fabricating data the assignment explicitly forbids.

## 3. Business-date vs. transaction-timestamp trap (real example)

File `SALES_S01_20240102.csv`, bill `S01/20240102/00029`:

```
S01/20240102/00029,1,P106490,2,528.59,SALE,2024-01-03T00:49:28
```

The file name says business date **2024-01-02**, but every line's `ts` is
**2024-01-03T00:49:28** (12:49 AM). This is the "bill punched after midnight
still belongs to the previous trading day" case the vendor describes.
`business_date` is parsed from the filename; `transaction_ts` is kept as-is
from the row. They intentionally disagree here, and a test asserts this.

## 4. Resend behaviour (real examples)

Compared line counts and `(bill_no, line_no)` sets between every `__R1` file
and its original in S01/S02:

| File pair | Original lines | Resend lines | Relationship |
|---|---|---|---|
| `SALES_S01_20240702` | 191 | 191 | byte-identical resend |
| `SALES_S01_20240914` | 433 | 433 | byte-identical resend |
| `SALES_S01_20241112` | 264 (263 keys) | 151 (150 keys) | **partial resend** — 113 keys present only in the original |
| `SALES_S01_20241217` | 248 | 248 | byte-identical resend |
| `SALES_S02_20240317` | 331 | 331 | byte-identical resend |

For `SALES_S01_20241112`, every one of the 150 `(bill_no,line_no)` keys that
*do* appear in the resend has byte-identical content to the original — the
resend is a strict subset, not a conflicting rewrite. This confirms:

* "newest file wins" would silently **drop 113 real sales lines**.
* Union-by-`(bill_no,line_no)` across original + all resends, keeping one
  copy of each key and flagging any key whose copies disagree in content, is
  the correct and sufficient strategy for this dataset. No actual content
  conflicts were found in the sampled pairs, but the pipeline still checks
  for and would flag them (`app/deduplication`).

## 5. Line-type / VOID semantics (real example)

File `SALES_S01_20240102.csv`, bill `S01/20240102/00017` — a fully cancelled
bill:

```
S01/20240102/00017,1,P106735,3,320.99,SALE,...
S01/20240102/00017,2,P107777,2,192.88,SALE,...
S01/20240102/00017,3,P100281,2,449.95,SALE,...
S01/20240102/00017,4,P101543,1,178.11,SALE,...
S01/20240102/00017,5,P106735,-3,320.99,VOID,...
S01/20240102/00017,6,P107777,-2,192.88,VOID,...
S01/20240102/00017,7,P100281,-2,449.95,VOID,...
S01/20240102/00017,8,P101543,-1,178.11,VOID,...
S01/20240102/00017,9,TAX,1,0.00,TAX,...
S01/20240102/00017,10,TENDER,1,0.00,TENDER,...
```

Each `SALE` line is mirrored by a `VOID` line with negated `qty` and the same
`unit_price`; `TAX` and `TENDER` are both zero. `SUM(qty*unit_price)` over
`SALE` rows only would (wrongly) show ₹1,857.87 of revenue for a bill the
customer paid ₹0 for. Keeping both `SALE` and `VOID` and applying
`qty * unit_price` uniformly nets this bill to exactly 0, which is the
behaviour implemented and tested.

## 6. Product code reissue (real example)

`masters.sql` products for code `P108206`:

```
(2173,'P108206','Daawat Poha 10kg','C04','Daawat','10kg','EA',
   DATE '2019-04-01', DATE '2024-05-31', FALSE),
(2217,'P108206','Local Mandi Apple 1kg','C12','Local Mandi','1kg','EA',
   DATE '2024-06-01', DATE '9999-12-31', TRUE),
```

Same `product_code`, two unrelated products (`C04` Staples & Grains vs. `C12`
Fruits & Vegetables), split exactly at `truth.json`'s stated `reissue_date`
of 2024-06-01. A join on `product_code` alone doubles every historical sale
of this code and misfiles half of them under the wrong category — resolving
`product_sk` via `product_code + business_date BETWEEN valid_from AND
valid_to` is required, and is what `app/enrichment` does.

## 7. Historical prices

`price_revisions` is keyed by `product_sk` (not `product_code`) with
`effective_from`/`effective_to`, e.g. for `product_sk=1001`:

```
(500001,1001, 98.50, 87.95, 2022-01-01, 2023-10-12)
(500002,1001,109.26, 97.55, 2023-10-13, 2023-11-08)
(500003,1001,115.86,103.45, 2023-11-09, 2024-06-13)
(500004,1001,128.09,114.37, 2024-06-14, 9999-12-31)
```

`selling_price` as-of `business_date` is taken as
`historical_authoritative_price`, resolved during curation and kept alongside
the untouched `source_unit_price` from the till export — neither field is
overwritten by the other.

## 8. Missing data (S07, July 2024)

Directory listing of `SALES_S07_202407*` shows every day present **except**:

```
SALES_S07_20240709   — missing
SALES_S07_20240710   — missing
SALES_S07_20240711   — missing
```

Matches `truth.json.missing_files` exactly. No source files exist for these
three dates; the pipeline does not fabricate zero-revenue fact rows for them
— `dim_date` still carries the calendar dates, but `fact_sales` simply has no
rows for `store_id='S07'` on those three dates, and the validation step
reports the gap explicitly rather than papering over it.

## 9. Master data (`masters.sql`)

Loads cleanly into PostgreSQL with `psql -f masters.sql`: `stores` (12),
`product_categories` (14), `products` (1,224, `product_code` intentionally
**not** unique — 24 codes appear twice), `price_revisions` (4,320, keyed by
`product_sk`). `product_sk` is treated as the only stable product identity
throughout the pipeline.

## 10. `_truth/` and `finance_monthly.csv`

Both are used exclusively for reconciliation after the curated fact table is
built (`app/validation`), never as pipeline inputs:

* `Question-1/data/_truth/truth.json` — expected file/row counts, monthly net
  revenue under two definitions (`monthly_net_revenue` = "true" POS-line
  truth including out-of-folder scope items, vs.
  `monthly_net_revenue_in_folder` = what a correct read of the sales folder
  alone should produce), a rounding-truth series, and prose notes explaining
  the three months that don't just "match" (March scope, July source gap,
  December rounding).
* `finance_monthly.csv` — finance's signed-off monthly numbers, which are a
  *different, valid* number for March (includes an institutional invoice
  never in the till exports) and December (rounds every bill to the rupee
  before summing) and intentionally cannot equal folder-only revenue for
  July (finance has S07's 3 missing days by phone, the folder never will).

The platform computes **POS-folder revenue** independently from the curated
fact table and reports it next to `finance_monthly.csv`/`truth.json` as a
reconciliation, explaining rather than erasing the differences.
