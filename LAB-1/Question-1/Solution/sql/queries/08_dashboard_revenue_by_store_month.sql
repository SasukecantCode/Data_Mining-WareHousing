-- Task 3 dashboard query: revenue by store and month.
-- TOTAL NET revenue (reconciles to the folder truth) -- includes SALE,
-- RETURN, DISCOUNT, VOID; excludes TAX/TENDER (both always 0 anyway).
SELECT
    f.store_id,
    s.store_name,
    d.year,
    d.month,
    d.year_month,
    ROUND(SUM(f.revenue_amount), 2) AS total_net_revenue
FROM fact_sales_dashboard f
JOIN dim_store s ON s.store_sk = f.store_sk
JOIN dim_date d ON d.date_sk = f.date_sk
GROUP BY f.store_id, s.store_name, d.year, d.month, d.year_month
ORDER BY d.year, d.month, f.store_id;
