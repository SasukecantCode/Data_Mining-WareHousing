-- Demonstrates source_unit_price (what the till printed) vs
-- historical_authoritative_price (resolved from price_revisions as-of
-- business_date). Both are kept, neither overwrites the other. The vendor
-- notes estimate a stale till price on "roughly one line in seventy"; the
-- measured rate across the full curated fact table is 11,079 / 745,860
-- SALE lines (~1 in 67), consistent with that estimate.
SELECT
    business_date,
    store_id,
    bill_no,
    line_no,
    product_sk,
    source_unit_price,
    historical_authoritative_price
FROM fact_sales
WHERE line_type = 'SALE'
  AND source_unit_price <> historical_authoritative_price
ORDER BY business_date
LIMIT 20;
