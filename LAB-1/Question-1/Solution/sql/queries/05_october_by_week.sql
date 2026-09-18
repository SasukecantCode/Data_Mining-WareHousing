SELECT
    d.iso_year,
    d.iso_week,
    MIN(f.business_date) AS week_start_in_october,
    ROUND(SUM(f.revenue_amount), 2) AS revenue
FROM fact_sales f
JOIN dim_date d ON d.business_date = f.business_date
WHERE f.business_date >= DATE '2024-10-01'
  AND f.business_date <  DATE '2024-11-01'
GROUP BY d.iso_year, d.iso_week
ORDER BY d.iso_year, d.iso_week;
