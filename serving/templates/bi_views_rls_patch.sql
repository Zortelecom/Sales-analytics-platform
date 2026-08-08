-- =============================================================================
-- bi_views_rls_patch.sql
--
-- Replacement definitions for five views in serving/templates/bi_views.sql.
-- Apply AFTER the main file, or fold these back into it (preferred):
--
--     duckdb data/warehouse/serving_dev.db < serving/templates/bi_views.sql
--     duckdb data/warehouse/serving_dev.db < serving/templates/bi_views_rls_patch.sql
--     python -m reporting.auth.introspect
--
-- WHY
-- ---
-- A supervisor is scoped by `subregion`. Five views can't be filtered on that
-- dimension as shipped, so a scoped user hits a fail-closed refusal on the
-- Sell-In / Sell-Out page and on anything reaching the product views:
--
--   v_sellin_sellout_kpi   no region, no subregion at all (only clientsd_id)
--   v_kp_sd_base           has region/subregion but no supervisor_name
--   v_kp_sd_monthly_kpi    same
--   v_kp_performance_kpi   region only
--   v_product_kpi          region only
--   v_innovation_kpi       region only
--
-- Each block below adds the missing dimension(s). Two of them change view
-- grain -- read the notes before applying.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 14a. v_kp_sd_base  (+ supervisor_name, + key_player)
--
-- dim_clientsd already carries supervisor_name and key_player; neither was
-- being projected. key_player is a bonus: see the note on 14c.
--
-- CAVEAT worth being deliberate about: dim_clientsd.supervisor_name is the
-- supervisor of the SUB-DISTRIBUTOR, while fact_sales.supervisor_name is the
-- supervisor of the SELLING REP. They are different attributes that happen to
-- align in most of the territory. Scoping supervisors by `subregions` avoids
-- having to decide which one is authoritative; if you scope by `supervisors`
-- instead, a supervisor's sell-in and sell-out numbers may cover slightly
-- different sets of SDs.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_kp_sd_base AS
SELECT
    f.kp_sd_line_id, f.sale_date, f.sale_year, f.sale_month,
    f.sku, f.product_category, f.clientsd_id, f.quantity, f.unit_price,
    f.total_amount, f.source_asserted_destocked,
    c.sd_name           AS client_name,
    c.region,
    c.subregion,
    c.city,
    c.supervisor_name,                       -- ADDED: enables supervisor RLS
    c.key_player,                            -- ADDED: enables a real KP rollup
    c.is_destocked      AS dim_is_destocked,
    CASE WHEN COALESCE(TRY_CAST(f.source_asserted_destocked AS BOOLEAN), FALSE)
              <> COALESCE(TRY_CAST(c.is_destocked AS BOOLEAN), FALSE)
         THEN TRUE ELSE FALSE END AS destocked_flag_mismatch
FROM bi.fact_kp_sd f
LEFT JOIN bi.dim_clientsd c ON f.clientsd_key = c.clientsd_key
    AND f.sale_date >= c.valid_from
    AND (f.sale_date < c.valid_to OR c.valid_to IS NULL);


-- ---------------------------------------------------------------------------
-- 14b. v_kp_sd_monthly_kpi  (+ supervisor_name, + key_player)
--
-- GRAIN NOTE: adding supervisor_name and key_player to a GROUP BY ALL adds
-- grouping columns. Both are functionally dependent on clientsd_id within a
-- period, so row count is unchanged in practice -- unless an SD changes
-- supervisor or key player mid-month, in which case that SD splits into two
-- rows. Downstream views aggregate, so totals stay correct either way.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_kp_sd_monthly_kpi AS
SELECT
    sale_year AS year, sale_month AS month,
    region, subregion, supervisor_name, key_player,
    clientsd_id, product_category,
    SUM(total_amount)                 AS sell_in_revenue,
    SUM(quantity)                     AS sell_in_units,
    COUNT(*)                          AS transaction_count,
    COUNT_IF(destocked_flag_mismatch) AS destocked_flag_mismatch_count
FROM v_kp_sd_base
GROUP BY ALL;


-- ---------------------------------------------------------------------------
-- 14c. v_kp_performance_kpi  (+ subregion, + supervisor_name, + key_player)
--
-- SEPARATE BUG, worth fixing while you're here: README.md describes this view
-- as "Revenue per Key Player across all its SDs", but the shipped definition
-- groups by region and product_category and never mentions key_player -- the
-- column wasn't available upstream. With key_player now flowing through
-- v_kp_sd_base it can group by the thing it is named after.
--
-- GRAIN CHANGE: this view now returns key_player x subregion rows where it
-- previously returned region rows. Nothing in the Streamlit app reads it
-- today, so there is no consumer to break -- but check any Power BI query
-- pointing at it before applying.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_kp_performance_kpi AS
SELECT
    year, month,
    key_player,                              -- ADDED: the actual grain
    region, subregion, supervisor_name,      -- ADDED: subregion, supervisor
    product_category,
    SUM(sell_in_revenue)                  AS sell_in_revenue,
    SUM(sell_in_units)                    AS sell_in_units,
    SUM(transaction_count)                AS transaction_count,
    SUM(destocked_flag_mismatch_count)    AS destocked_flag_mismatch_count,
    COUNT(DISTINCT clientsd_id)           AS active_sds
FROM v_kp_sd_monthly_kpi
GROUP BY ALL;


-- ---------------------------------------------------------------------------
-- 14d. v_sellin_sellout_kpi  (+ region, + subregion, + supervisor_name)
--
-- The most important one: as shipped this view exposes only clientsd_id, so a
-- subregion-scoped supervisor cannot read the Sell-In / Sell-Out page at all.
--
-- The geography columns are carried as MAX(...) aggregates rather than added
-- to the GROUP BY. That is deliberate: if an SD moved subregion mid-period,
-- grouping on it would split the SD into two rows in each CTE and the FULL
-- OUTER JOIN would fan out 2x2. MAX() keeps the join grain at
-- (year, month, clientsd_id, product_category) exactly as before, and the SD
-- resolves to one geography. They stay out of the join key for the same
-- reason -- if the two sides disagreed, the join would silently drop rows.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_sellin_sellout_kpi AS
WITH sell_in AS (
    SELECT
        year, month, clientsd_id, product_category,
        MAX(region)           AS region,
        MAX(subregion)        AS subregion,
        MAX(supervisor_name)  AS supervisor_name,
        SUM(sell_in_revenue)  AS sell_in_revenue,
        SUM(sell_in_units)    AS sell_in_units
    FROM v_kp_sd_monthly_kpi
    GROUP BY year, month, clientsd_id, product_category
), sell_out AS (
    SELECT
        sale_year AS year, sale_month AS month, clientsd_id, product_category,
        -- v_sales_base carries BOTH the rep's geography (region/subregion)
        -- and the SD's (client_region/client_subregion). Use the SD's here so
        -- the sell-out side is scoped on the same axis as the sell-in side.
        MAX(client_region)    AS region,
        MAX(client_subregion) AS subregion,
        MAX(supervisor_name)  AS supervisor_name,
        SUM(total_amount)     AS sell_out_revenue,
        SUM(quantity)         AS sell_out_units
    FROM v_sales_base
    GROUP BY sale_year, sale_month, clientsd_id, product_category
)
SELECT
    COALESCE(i.year, o.year)                            AS year,
    COALESCE(i.month, o.month)                          AS month,
    COALESCE(i.clientsd_id, o.clientsd_id)              AS clientsd_id,
    COALESCE(i.product_category, o.product_category)    AS product_category,
    COALESCE(i.region, o.region)                        AS region,            -- ADDED
    COALESCE(i.subregion, o.subregion)                  AS subregion,         -- ADDED
    COALESCE(i.supervisor_name, o.supervisor_name)      AS supervisor_name,   -- ADDED
    COALESCE(i.sell_in_revenue, 0)                      AS sell_in_revenue,
    COALESCE(o.sell_out_revenue, 0)                     AS sell_out_revenue,
    COALESCE(i.sell_in_units, 0)                        AS sell_in_units,
    COALESCE(o.sell_out_units, 0)                       AS sell_out_units,
    CASE WHEN COALESCE(i.sell_in_revenue, 0) > 0
         THEN ROUND(COALESCE(o.sell_out_revenue, 0) / i.sell_in_revenue * 100, 2)
    END AS sell_through_pct
FROM sell_in i
FULL OUTER JOIN sell_out o
  ON i.year = o.year AND i.month = o.month AND i.clientsd_id = o.clientsd_id
 AND i.product_category = o.product_category;


-- ---------------------------------------------------------------------------
-- 8b / 9b. v_product_kpi and v_innovation_kpi  (+ subregion)
--
-- GRAIN CHANGE: both views gain a grouping column, so a SKU sold in three
-- subregions now returns three rows instead of one. queries.py does not read
-- either view (product pages aggregate v_monthly_kpi), so the app is
-- unaffected -- but any Power BI measure pointed at them must SUM rather than
-- read a single row. Skip this block if you'd rather leave them region-only;
-- in that case remove the two [PATCH] `subregions` entries from
-- SCOPE_COLUMNS in reporting/auth/rls.py, and a subregion-scoped supervisor
-- will get a clean refusal instead of silently seeing regional totals.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_product_kpi AS
SELECT
    sb.sale_year               AS year,
    sb.sale_month              AS month,
    sb.region,
    sb.subregion,                                -- ADDED
    sb.product_category,
    sb.product_subcategory,
    sb.product_name,
    sb.sku,
    sb.is_innovation_product,
    SUM(sb.total_amount)           AS revenue,
    SUM(sb.quantity)               AS units_sold,
    SUM(sb.total_weight_kg)        AS weight_kg,
    COUNT(DISTINCT sb.clientsd_id) AS active_clients,
    AVG(sb.price_variance_pct)     AS avg_price_variance_pct
FROM v_sales_base sb
GROUP BY ALL;


CREATE OR REPLACE VIEW v_innovation_kpi AS
SELECT
    sale_year               AS year,
    sale_month              AS month,
    region,
    subregion,                                   -- ADDED
    product_category,
    is_innovation_product,
    SUM(total_amount)       AS revenue,
    SUM(quantity)           AS units_sold,
    COUNT(DISTINCT clientsd_id) AS active_clients
FROM v_sales_base
GROUP BY ALL;
