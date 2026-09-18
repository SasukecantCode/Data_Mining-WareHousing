-- Demonstrates that product_code 'P108206' is NOT a single product: it was
-- 'Daawat Poha 10kg' (product_sk 2173, category C04) up to 2024-05-31, and
-- became 'Local Mandi Apple 1kg' (product_sk 2217, category C12) from
-- 2024-06-01, matching masters.sql and the reissue_date in _truth/truth.json.
-- Sales before and after the reissue resolve to different product_sk values
-- even though the till printed the same product_code on the bill.
SELECT
    f.product_code,
    f.product_sk,
    p.product_name,
    p.category_id,
    MIN(f.business_date) AS first_sale,
    MAX(f.business_date) AS last_sale,
    COUNT(*) AS lines,
    ROUND(SUM(f.revenue_amount), 2) AS revenue
FROM fact_sales f
JOIN dim_product p ON p.product_sk = f.product_sk
WHERE f.product_code = 'P108206'
GROUP BY f.product_code, f.product_sk, p.product_name, p.category_id
ORDER BY first_sale;
