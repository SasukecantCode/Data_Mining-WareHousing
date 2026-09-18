-- Task 3 "important dashboard attribution rule" check: TOTAL NET revenue
-- (all revenue-bearing line types) is NOT the same as PRODUCT-ATTRIBUTED
-- revenue (only lines with a resolved product_sk). The gap is every line
-- that has no product identity: bill-level DISCOUNT, TAX (always 0),
-- TENDER (always 0), AND -- a real edge case in this dataset -- a VOID
-- that cancels a DISCOUNT line when a whole bill is cancelled (product_code
-- 'DISC', line_type 'VOID': still not a product, even though line_type is
-- normally product-bearing). All of these deliberately have product_sk =
-- NULL, so "product_sk IS NULL" is exactly the non-product-revenue bucket
-- -- no separate per-line-type subtraction needed, and no allocation of
-- DISCOUNT across products/categories is performed. If a dashboard needs
-- category-level net revenue to foot to the total, that requires a
-- separate, explicitly documented allocation rule (e.g. pro-rata by each
-- category's share of that bill's SALE revenue) -- deliberately NOT
-- implemented, since the source data does not specify one.
SELECT
    ROUND(SUM(revenue_amount), 2) AS total_net_revenue,
    ROUND(SUM(CASE WHEN product_sk IS NOT NULL THEN revenue_amount ELSE 0 END), 2) AS product_attributed_revenue,
    ROUND(SUM(CASE WHEN product_sk IS NULL THEN revenue_amount ELSE 0 END), 2) AS non_product_revenue,
    ROUND(SUM(CASE WHEN line_type = 'DISCOUNT' THEN revenue_amount ELSE 0 END), 2) AS of_which_discount,
    ROUND(SUM(CASE WHEN product_sk IS NULL AND line_type = 'VOID' THEN revenue_amount ELSE 0 END), 2) AS of_which_void_of_discount,
    ROUND(
        SUM(revenue_amount)
        - SUM(CASE WHEN product_sk IS NOT NULL THEN revenue_amount ELSE 0 END)
        - SUM(CASE WHEN product_sk IS NULL THEN revenue_amount ELSE 0 END)
    , 2) AS unexplained_gap  -- must be 0.00 by construction (every row is in exactly one bucket)
FROM fact_sales_dashboard
WHERE business_date >= DATE '2024-10-01' AND business_date < DATE '2024-11-01';
