-- Task 3 dashboard query: PRODUCT-ATTRIBUTED revenue by category.
-- category_id/category_sk is reached ONLY via product_sk -> dim_product ->
-- dim_category -- never stored on the fact row, and never guessed for a
-- DISCOUNT/TAX/TENDER line (which have no category, not even 'other').
SELECT
    cat.category_sk,
    cat.category_id,
    cat.category_name,
    cat.department,
    ROUND(SUM(f.revenue_amount), 2) AS product_attributed_revenue
FROM fact_sales_dashboard f
JOIN dim_product p ON p.product_sk = f.product_sk
JOIN dim_category cat ON cat.category_sk = p.category_sk
GROUP BY cat.category_sk, cat.category_id, cat.category_name, cat.department
ORDER BY product_attributed_revenue DESC;
