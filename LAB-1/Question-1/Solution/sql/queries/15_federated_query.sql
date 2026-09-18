-- Task 5: ONE query, joining live across two systems, neither copied into
-- the other:
--
--   * f  -- sales fact data: read_parquet() straight out of MinIO (the
--           Task 1/2 curated Parquet, s3://annapurna/curated/sales/...)
--   * s, p, cat -- store/product/category dimensions: pg.stores / pg.products
--           / pg.product_categories, read live through DuckDB's `postgres`
--           ATTACH (app/duck_federated.py) -- no Parquet snapshot of these
--           tables exists anywhere for this query to use.
--
-- product_sk is the existing Task 3 historical-identity surrogate key,
-- already resolved once during Task 1 curation (product_code + business_date
-- -> product_sk) -- this query does a plain equi-join on it, no re-resolution.
--
-- Only $start_date/$end_date vary between runs; the query text is identical.
SELECT
    s.store_id,
    s.store_name,
    cat.category_id,
    cat.category_name,
    ROUND(SUM(f.revenue_amount), 2) AS product_attributed_revenue
FROM read_parquet('s3://annapurna/curated/sales/*/*/*/sales.parquet', hive_partitioning = true) f
JOIN pg.stores s              ON s.store_id    = f.store_id
JOIN pg.products p            ON p.product_sk  = f.product_sk
JOIN pg.product_categories cat ON cat.category_id = p.category_id
WHERE f.business_date >= $start_date AND f.business_date < $end_date
GROUP BY s.store_id, s.store_name, cat.category_id, cat.category_name
ORDER BY product_attributed_revenue DESC;
