-- Task 3 dashboard query: revenue by store + category + month
-- (PRODUCT-ATTRIBUTED -- see 13_total_vs_product_attributed_revenue.sql for
-- the reconciliation against total net revenue).
--
-- The f.business_date predicate is repeated even though dim_date is already
-- joined and filtered on d.year/d.month: filtering the FACT table's own
-- business_date directly (not only the joined dimension) is what lets
-- DuckDB prune curated files by store, per scripts/14_dashboard_pruning_check.py.
SELECT
    s.store_id,
    s.store_name,
    cat.category_id,
    cat.category_name,
    d.year,
    d.month,
    ROUND(SUM(f.revenue_amount), 2) AS product_attributed_revenue
FROM fact_sales_dashboard f
JOIN dim_store s ON s.store_sk = f.store_sk
JOIN dim_product p ON p.product_sk = f.product_sk
JOIN dim_category cat ON cat.category_sk = p.category_sk
JOIN dim_date d ON d.date_sk = f.date_sk
WHERE f.business_date >= DATE '2024-10-01' AND f.business_date < DATE '2024-11-01'
GROUP BY s.store_id, s.store_name, cat.category_id, cat.category_name, d.year, d.month
ORDER BY product_attributed_revenue DESC
LIMIT 20;
