-- Task 3 dashboard query: total net revenue by day of week.
-- Weekday convention: Monday=1 ... Sunday=7 (ISO 8601), set in dim_date.
SELECT
    d.day_of_week,
    d.day_name,
    ROUND(SUM(f.revenue_amount), 2) AS total_net_revenue,
    COUNT(*) AS lines
FROM fact_sales_dashboard f
JOIN dim_date d ON d.date_sk = f.date_sk
GROUP BY d.day_of_week, d.day_name
ORDER BY d.day_of_week;
