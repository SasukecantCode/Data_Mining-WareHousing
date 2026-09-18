-- Task 3 dashboard query: PRODUCT-ATTRIBUTED revenue by product.
-- Only rows with a resolved product_sk contribute (SALE/RETURN/VOID) --
-- DISCOUNT/TAX/TENDER have no product identity and are correctly excluded
-- (an inner join to dim_product drops them since product_sk IS NULL there).
-- This total is therefore LESS than total net revenue by the sum of
-- DISCOUNT lines -- see 12_total_vs_product_attributed_revenue.sql.
SELECT
    p.product_sk,
    p.product_code,
    p.product_name,
    ROUND(SUM(f.revenue_amount), 2) AS product_attributed_revenue,
    SUM(CASE WHEN f.line_type = 'SALE' THEN f.qty ELSE 0 END) AS units_sold
FROM fact_sales_dashboard f
JOIN dim_product p ON p.product_sk = f.product_sk
GROUP BY p.product_sk, p.product_code, p.product_name
ORDER BY product_attributed_revenue DESC
LIMIT 20;
