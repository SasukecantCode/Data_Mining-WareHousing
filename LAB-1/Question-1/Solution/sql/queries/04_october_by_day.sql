SELECT
    business_date,
    ROUND(SUM(revenue_amount), 2) AS revenue
FROM fact_sales
WHERE business_date >= DATE '2024-10-01'
  AND business_date <  DATE '2024-11-01'
GROUP BY business_date
ORDER BY business_date;
