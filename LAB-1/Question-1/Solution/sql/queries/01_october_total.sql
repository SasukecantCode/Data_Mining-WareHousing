-- October 2024 total revenue, computed from curated fact_sales (never hard-coded).
SELECT
    ROUND(SUM(revenue_amount), 2) AS october_2024_revenue
FROM fact_sales
WHERE business_date >= DATE '2024-10-01'
  AND business_date <  DATE '2024-11-01';
