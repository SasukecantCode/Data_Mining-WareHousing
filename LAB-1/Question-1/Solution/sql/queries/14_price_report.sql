-- Task 4: price report for a parameterized reporting period
-- [$start_date, $end_date]. THE SAME QUERY TEXT runs for any period --
-- only the two date parameters change between runs (see
-- scripts/12_task4_price_report.py, which executes this exact string twice).
--
-- "The applicable price" for the period is resolved as the price_revisions
-- row effective on the period's END date (product_sk + effective_from/
-- effective_to + reporting date, per the task spec) -- never the current/
-- latest revision, never ORDER BY ... DESC LIMIT 1. If no revision covers
-- end_date, applicable_price is NULL and price_missing_for_period = TRUE
-- (surfaced, not silently substituted with another period's price).
--
-- Multiple revisions in one period are handled explicitly, not silently
-- collapsed: if the revision effective at start_date differs from the one
-- effective at end_date, price_changed_during_period = TRUE and the prior
-- (period-start) price is also returned for comparison.
WITH at_period_end AS (
    SELECT product_sk, revision_id, mrp, selling_price, effective_from, effective_to
    FROM price_revisions
    WHERE effective_from <= $end_date AND effective_to >= $end_date
),
at_period_start AS (
    SELECT product_sk, revision_id AS start_revision_id,
           selling_price AS price_at_period_start
    FROM price_revisions
    WHERE effective_from <= $start_date AND effective_to >= $start_date
)
SELECT
    p.product_sk,
    p.product_code,
    p.product_name,
    e.selling_price                                   AS applicable_price,
    e.mrp                                              AS applicable_mrp,
    e.effective_from,
    e.effective_to,
    s.price_at_period_start,
    e.revision_id IS NULL                              AS price_missing_for_period,
    (e.revision_id IS DISTINCT FROM s.start_revision_id
        AND e.revision_id IS NOT NULL AND s.start_revision_id IS NOT NULL)
                                                        AS price_changed_during_period
FROM dim_product p
LEFT JOIN at_period_end e   ON e.product_sk = p.product_sk
LEFT JOIN at_period_start s ON s.product_sk = p.product_sk
ORDER BY p.product_sk;
