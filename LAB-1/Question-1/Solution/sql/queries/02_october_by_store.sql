-- October 2024 revenue by store. store partition pruning means only the 12
-- store=S../year=2024/month=10 files are read, nothing else in curated/.
SELECT
    f.store_id,
    d.store_name,
    d.city,
    ROUND(SUM(f.revenue_amount), 2) AS revenue
FROM fact_sales f
JOIN dim_store d ON d.store_id = f.store_id
WHERE f.business_date >= DATE '2024-10-01'
  AND f.business_date <  DATE '2024-11-01'
GROUP BY f.store_id, d.store_name, d.city
ORDER BY revenue DESC;
