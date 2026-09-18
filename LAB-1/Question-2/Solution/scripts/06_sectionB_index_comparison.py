"""
Section B(d) -- access-method comparison, measured, not asserted.

The retrieval query the application actually runs at lookup time (given a
notice, find everyone sharing a band with it):

    SELECT DISTINCT m2.notice_id
    FROM lsh_bucket_members m1
    JOIN lsh_bucket_members m2
      ON m1.band_no = m2.band_no AND m1.bucket_hash = m2.bucket_hash
    WHERE m1.notice_id = %s AND m2.notice_id <> %s;

m1's side is already covered by the table's PRIMARY KEY (notice_id,
band_no). The access-method decision this section is about is what index
serves the m2 side: a lookup by (band_no, bucket_hash) equality, always
equality, never a range. Three configurations are built and measured, one
at a time (the competing index is dropped so EXPLAIN is forced to use
what's actually there, not the theory of what it could use):

  1. BTREE composite index  on (band_no, bucket_hash)   -- rejected
  2. HASH index             on (bucket_hash)            -- adopted
  3. no index (seq scan)                                -- floor, for scale

Run:
    .venv/bin/python scripts/06_sectionB_index_comparison.py
Output: reports/taskB/index_comparison.txt
"""
import os
import random
import statistics
import time
from pathlib import Path

import psycopg

DSN = (
    f"host=localhost port={os.environ.get('POSTGRES_PORT', 5433)} "
    f"dbname={os.environ.get('POSTGRES_DB', 'setubid')} "
    f"user={os.environ.get('POSTGRES_USER', 'setubid')} "
    f"password={os.environ.get('POSTGRES_PASSWORD', 'setubid_dev_password')}"
)
REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "taskB"

QUERY = """
SELECT DISTINCT m2.notice_id
FROM lsh_bucket_members m1
JOIN lsh_bucket_members m2
  ON m1.band_no = m2.band_no AND m1.bucket_hash = m2.bucket_hash
WHERE m1.notice_id = %s AND m2.notice_id <> %s;
"""

CONFIGS = {
    "btree_composite (band_no, bucket_hash) -- rejected": [
        "DROP INDEX IF EXISTS ix_hash_bucket;",
        "DROP INDEX IF EXISTS ix_btree_bucket;",
        "CREATE INDEX ix_btree_bucket ON lsh_bucket_members USING btree (band_no, bucket_hash);",
        "ANALYZE lsh_bucket_members;",
    ],
    "hash (bucket_hash) -- adopted": [
        "DROP INDEX IF EXISTS ix_btree_bucket;",
        "DROP INDEX IF EXISTS ix_hash_bucket;",
        "CREATE INDEX ix_hash_bucket ON lsh_bucket_members USING hash (bucket_hash);",
        "ANALYZE lsh_bucket_members;",
    ],
    "no index (seq scan) -- floor": [
        "DROP INDEX IF EXISTS ix_btree_bucket;",
        "DROP INDEX IF EXISTS ix_hash_bucket;",
        "ANALYZE lsh_bucket_members;",
    ],
}


def explain_analyze(cur, notice_id):
    cur.execute(
        "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + QUERY, (notice_id, notice_id)
    )
    return "\n".join(r[0] for r in cur.fetchall())


def parse_plan_summary(plan_text):
    lines = plan_text.splitlines()
    node_line = next((l for l in lines if "Scan" in l or "Loop" in l or "Join" in l), lines[0])
    time_line = next((l for l in lines if l.strip().startswith("Execution Time")), None)
    buf_lines = [l.strip() for l in lines if "Buffers:" in l]
    return node_line.strip(), time_line.strip() if time_line else "?", buf_lines


def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT notice_id FROM notices ORDER BY notice_id;")
            all_ids = [r[0] for r in cur.fetchall()]
        random.seed(7)
        sample_ids = random.sample(all_ids, 30)

        out = []
        example_plans = {}
        for label, ddl_statements in CONFIGS.items():
            with conn.cursor() as cur:
                for stmt in ddl_statements:
                    cur.execute(stmt)

            timings = []
            example_plan = None
            with conn.cursor() as cur:
                for nid in sample_ids:
                    t0 = time.perf_counter()
                    cur.execute(QUERY, (nid, nid))
                    cur.fetchall()
                    timings.append((time.perf_counter() - t0) * 1000)
                # one EXPLAIN ANALYZE captured verbatim for the report
                example_plan = explain_analyze(cur, sample_ids[0])

            example_plans[label] = example_plan
            node, exec_time, bufs = parse_plan_summary(example_plan)
            out.append(f"=== {label} ===")
            out.append(f"  planner node (example notice {sample_ids[0]}): {node}")
            out.append(f"  {bufs[0] if bufs else 'Buffers: (none reported)'}")
            out.append(f"  EXPLAIN ANALYZE {exec_time}")
            out.append(
                f"  wall-clock over {len(sample_ids)} distinct notices (python-timed, incl. round-trip): "
                f"mean={statistics.mean(timings):.3f}ms  median={statistics.median(timings):.3f}ms  "
                f"p95={sorted(timings)[int(0.95*len(timings))]:.3f}ms"
            )
            out.append("")

        out.append("=== Full EXPLAIN (ANALYZE, BUFFERS) for one representative notice, all three configs ===")
        for label, plan in example_plans.items():
            out.append(f"--- {label} ---")
            out.append(plan)
            out.append("")

        text = "\n".join(out)
        print(text)
        with open(REPORT_DIR / "index_comparison.txt", "w", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
