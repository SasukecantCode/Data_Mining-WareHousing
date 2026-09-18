-- October 2024 revenue by product. product_sk was resolved as-of business_date
-- during curation, so this is a plain surrogate-key join -- no temporal logic
-- needed at query time.
SELECT
    f.product_sk,
    p.product_name,
    p.category_id,
    ROUND(SUM(f.revenue_amount), 2) AS revenue,
    SUM(CASE WHEN f.line_type = 'SALE' THEN f.qty ELSE 0 END) AS units_sold
FROM fact_sales f
JOIN dim_product p ON p.product_sk = f.product_sk
WHERE f.business_date >= DATE '2024-10-01'
  AND f.business_date <  DATE '2024-11-01'
GROUP BY f.product_sk, p.product_name, p.category_id
ORDER BY revenue DESC
LIMIT 20;
