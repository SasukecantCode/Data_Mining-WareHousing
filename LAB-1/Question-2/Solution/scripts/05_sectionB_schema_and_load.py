"""
Section B(d) -- give the retrieval structure a home and an access path.

The LSH banding built in Section A(c) (k=126 MinHash, r=2 rows/band,
b=63 bands -- the adopted operating point) lived entirely in a Python
dict that dies with the process. This script gives it a real, restart-
durable home: a PostgreSQL schema, loaded once, queried by the
application through SQL -- not recomputed from a pickle every run.

Schema
------
notices(notice_id PK, portal_id, published_at)
lsh_bucket_members(notice_id FK, band_no, bucket_hash)
    one row per (notice, band) -- 12,000 notices x 63 bands = 756,000 rows.
    PRIMARY KEY (notice_id, band_no) covers "give me notice X's own bands".
    The access-method choice this section argues for is the index that
    answers the *other* direction of the query -- "who else is in this
    (band_no, bucket_hash) bucket" -- which is what candidate retrieval
    actually runs at lookup time.

Run:
    docker compose up -d
    .venv/bin/python scripts/05_sectionB_schema_and_load.py
"""
import hashlib
import os
from pathlib import Path

import psycopg
import importlib.util

spec_a = importlib.util.spec_from_file_location(
    "sectionA", Path(__file__).parent / "01_sectionA_similarity_score.py"
)
sectionA = importlib.util.module_from_spec(spec_a)
spec_a.loader.exec_module(sectionA)

spec_b = importlib.util.spec_from_file_location(
    "sectionB", Path(__file__).parent / "03_sectionA_minhash_sketch.py"
)
sectionB = importlib.util.module_from_spec(spec_b)
spec_b.loader.exec_module(sectionB)

K = 126
R, B = 2, 63  # adopted operating point, Section A(c)

DSN = (
    f"host=localhost port={os.environ.get('POSTGRES_PORT', 5433)} "
    f"dbname={os.environ.get('POSTGRES_DB', 'setubid')} "
    f"user={os.environ.get('POSTGRES_USER', 'setubid')} "
    f"password={os.environ.get('POSTGRES_PASSWORD', 'setubid_dev_password')}"
)

SCHEMA_SQL = """
DROP TABLE IF EXISTS lsh_bucket_members;
DROP TABLE IF EXISTS notices;

CREATE TABLE notices (
    notice_id    TEXT PRIMARY KEY,
    portal_id    TEXT NOT NULL,
    published_at DATE NOT NULL
);

CREATE TABLE lsh_bucket_members (
    notice_id    TEXT NOT NULL REFERENCES notices(notice_id),
    band_no      SMALLINT NOT NULL,
    bucket_hash  BIGINT NOT NULL,
    PRIMARY KEY (notice_id, band_no)
);
"""


def bucket_hash_signed64(band_slice_bytes):
    digest = hashlib.blake2b(band_slice_bytes, digest_size=8).digest()
    val = int.from_bytes(digest, "big")
    return val - (1 << 64) if val >= (1 << 63) else val


def main():
    notices = sectionA.load_notices()
    stopwords, structural_cache = sectionA.build_corpus_stopwords(notices, 0.40)
    notice_ids = sorted(notices.keys())
    sets = {nid: sectionA.choice2_tokens(structural_cache[nid], stopwords) for nid in notice_ids}

    all_tokens = set()
    for s in sets.values():
        all_tokens.update(s)
    base_cache = {t: sectionB.base_hash(t) for t in all_tokens}

    print("Building padded matrix + k=126 MinHash signatures for all 12,000 notices ...")
    mat = sectionB.build_padded_matrix(notice_ids, sets, base_cache)
    a_arr, b_arr = sectionB.make_hash_family(K, seed=42)
    sig = sectionB.signatures_for_matrix(mat, a_arr, b_arr)
    print("Signatures ready:", sig.shape)

    with psycopg.connect(DSN, autocommit=True) as conn:
        with conn.cursor() as cur:
            print("(Re)creating schema ...")
            cur.execute(SCHEMA_SQL)

            print("Loading notices table ...")
            with cur.copy("COPY notices (notice_id, portal_id, published_at) FROM STDIN") as copy:
                for nid in notice_ids:
                    row = notices[nid]
                    copy.write_row((nid, row["portal_id"], row["published_at"]))

            print(f"Loading lsh_bucket_members (r={R}, b={B}: {len(notice_ids)}x{B} = "
                  f"{len(notice_ids)*B:,} rows) ...")
            with cur.copy(
                "COPY lsh_bucket_members (notice_id, band_no, bucket_hash) FROM STDIN"
            ) as copy:
                for i, nid in enumerate(notice_ids):
                    row = sig[i]
                    for band in range(B):
                        start = band * R
                        bh = bucket_hash_signed64(row[start : start + R].tobytes())
                        copy.write_row((nid, band, bh))

            cur.execute("ANALYZE notices;")
            cur.execute("ANALYZE lsh_bucket_members;")

            cur.execute("SELECT count(*) FROM lsh_bucket_members;")
            print("Rows loaded:", cur.fetchone()[0])

    print("Done. Schema + data persisted in PostgreSQL (setubid_postgres, port 5433).")


if __name__ == "__main__":
    main()
