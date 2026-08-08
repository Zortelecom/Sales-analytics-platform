-- =============================================================================
-- BI VIEWS — SEMANTIC KPI LAYER
-- serving/templates/bi_views.sql
--
-- USAGE:
--   duckdb data/warehouse/serving_dev.db < serving/templates/bi_views.sql
--
-- All source tables are schema-qualified as "bi.<table>".
-- If your schema differs, do a find-replace on "bi." before running.
-- Views are created in the default "main" schema (no prefix needed in queries).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. BASE SALES VIEW — denormalized, joins all dimensions
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_sales_base AS
SELECT
    fs.sales_line_id,
    fs.sale_date,
    fs.sale_year,
    fs.sale_month,
    dd.week_of_year,
    dd.week_of_month,
    dd.quarter,
    dd.fiscal_year,
    dd.fiscal_month,
    dd.day_of_week,

    -- Product
    fs.product_key,
    fs.sku,
    fs.product_category,
    dp.product_name,
    dp.product_subcategory,
    dp.is_innovation_product,

    -- Salesperson
    fs.salesperson_key,
    fs.salesperson_id,
    fs.salesperson_name,
    fs.supervisor_name,
    fs.sales_channel,
    fs.region,
    fs.subregion,

    -- Client / SD
    fs.clientsd_key,
    fs.clientsd_id,
    dc.sd_name          AS client_name,
    dc.region           AS client_region,
    dc.subregion        AS client_subregion,
    dc.city             AS client_city,
    dc.is_destocked,

    -- Measures
    fs.quantity,
    fs.unit_price_actual,
    fs.unit_price_standard,
    fs.total_amount,
    fs.unit_weight_actual,
    fs.unit_weight_standard,
    fs.total_weight_kg,
    fs.price_variance_pct

FROM bi.fact_sales fs
LEFT JOIN bi.dim_date        dd ON fs.date_key        = dd.date_key
-- SCD Type 2 guard: match the exact dimension version valid at sale time
LEFT JOIN bi.dim_products    dp ON fs.product_key     = dp.product_key
                                AND (fs.sale_date >= dp.valid_from AND (dp.valid_to IS NULL OR fs.sale_date < dp.valid_to))
-- dim_salesperson join removed: all salesperson fields (salesperson_name, region,
-- subregion, sales_channel, supervisor_name) are already denormalized into fact_sales.
-- The join added no columns but caused row fan-out on SCD Type 2 history rows.
LEFT JOIN bi.dim_clientsd    dc ON fs.clientsd_key    = dc.clientsd_key
                                AND (fs.sale_date >= dc.valid_from AND (dc.valid_to IS NULL OR fs.sale_date < dc.valid_to));


-- ---------------------------------------------------------------------------
-- 2. TARGET BASE VIEW
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_targets_base AS
SELECT
    ft.target_line_id,
    ft.target_year,
    ft.target_month,
    ft.target_month_num,
    ft.salesperson_id,
    ft.salesperson_key,
    ft.product_category,
    ft.target_amount,
    ds.salesperson_name,
    ds.region,
    ds.subregion,
    ds.supervisor_name,
    ds.sales_channel
FROM bi.fact_targets ft
LEFT JOIN bi.dim_salesperson ds ON ft.salesperson_key = ds.salesperson_key;


-- ---------------------------------------------------------------------------
-- 3. MONTHLY KPI — core aggregation grain
-- ---------------------------------------------------------------------------
-- region excluded from join key: SCD changes split sales across regions
-- but targets are salesperson-keyed only. Joining on region would orphan
-- targets when a salesperson moves, producing duplicate rows.
-- Known tradeoff: if one salesperson sells in two regions within the same
-- month, both sales rows match the single target row and SUM(target)
-- double-counts downstream. v_quarterly_kpi follows the same rule.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_monthly_kpi AS
WITH sales_agg AS (
    SELECT
        sale_year,
        sale_month,
        region,
        subregion,
        salesperson_id,
        salesperson_name,
        supervisor_name,
        sales_channel,
        product_category,
        SUM(total_amount)           AS revenue,
        SUM(quantity)               AS units_sold,
        SUM(total_weight_kg)        AS weight_kg,
        COUNT(DISTINCT clientsd_id) AS active_clients,
        AVG(price_variance_pct)     AS avg_price_variance_pct,
        COUNT(sales_line_id)        AS transaction_count
    FROM v_sales_base
    GROUP BY ALL
),
target_agg AS (
    SELECT
        target_year,
        target_month_num            AS target_month,
        region,
        subregion,
        salesperson_id,
        salesperson_name,
        supervisor_name,
        sales_channel,
        product_category,
        SUM(target_amount)          AS target
    FROM v_targets_base
    GROUP BY ALL
)
SELECT
    COALESCE(s.sale_year,        t.target_year)        AS year,
    COALESCE(s.sale_month,       t.target_month)       AS month,
    COALESCE(s.region,           t.region)             AS region,
    COALESCE(s.subregion,        t.subregion)          AS subregion,
    COALESCE(s.salesperson_id,   t.salesperson_id)     AS salesperson_id,
    COALESCE(s.salesperson_name, t.salesperson_name)   AS salesperson_name,
    COALESCE(s.supervisor_name,  t.supervisor_name)    AS supervisor_name,
    COALESCE(s.sales_channel,    t.sales_channel)      AS sales_channel,
    COALESCE(s.product_category, t.product_category)   AS product_category,
    COALESCE(s.revenue,          0)                    AS revenue,
    COALESCE(t.target,           0)                    AS target,
    COALESCE(s.units_sold,       0)                    AS units_sold,
    COALESCE(s.weight_kg,        0)                    AS weight_kg,
    COALESCE(s.active_clients,   0)                    AS active_clients,
    COALESCE(s.avg_price_variance_pct, 0)              AS avg_price_variance_pct,
    COALESCE(s.transaction_count, 0)                   AS transaction_count,
    CASE
        WHEN COALESCE(t.target, 0) > 0
        THEN ROUND(COALESCE(s.revenue, 0) / t.target * 100, 2)
        ELSE NULL
    END AS achievement_pct
FROM sales_agg s
FULL OUTER JOIN target_agg t
    ON  s.sale_year        = t.target_year
    AND s.sale_month       = t.target_month
    AND s.salesperson_id   = t.salesperson_id
    AND s.product_category = t.product_category;


-- ---------------------------------------------------------------------------
-- 4. YTD KPI
-- NOTE: ytd_active_clients sums monthly distinct-client counts, so a client
-- active in several months is counted once per month (upper bound, not
-- distinct YTD clients). Query v_sales_base directly for a true distinct.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_ytd_kpi AS
SELECT
    year,
    region,
    subregion,
    salesperson_id,
    salesperson_name,
    supervisor_name,
    sales_channel,
    product_category,
    SUM(revenue)        AS ytd_revenue,
    SUM(target)         AS ytd_target,
    SUM(units_sold)     AS ytd_units,
    SUM(weight_kg)      AS ytd_weight_kg,
    SUM(active_clients) AS ytd_active_clients,
    CASE WHEN SUM(target) > 0
         THEN ROUND(SUM(revenue) / SUM(target) * 100, 2) ELSE NULL
    END AS ytd_achievement_pct
FROM v_monthly_kpi
GROUP BY ALL;


-- ---------------------------------------------------------------------------
-- 5. WEEKLY KPI
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_weekly_kpi AS
SELECT
    sale_year,
    sale_month,
    week_of_month,
    week_of_year,
    region,
    subregion,
    salesperson_id,
    salesperson_name,
    supervisor_name,
    product_category,
    SUM(total_amount)           AS revenue,
    SUM(quantity)               AS units_sold,
    SUM(total_weight_kg)        AS weight_kg,
    COUNT(DISTINCT clientsd_id) AS active_clients,
    COUNT(sales_line_id)        AS transaction_count
FROM v_sales_base
GROUP BY ALL;


-- ---------------------------------------------------------------------------
-- 6. REGIONAL KPI
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_regional_kpi AS
SELECT
    year,
    month,
    region,
    subregion,
    SUM(revenue)           AS revenue,
    SUM(target)            AS target,
    SUM(units_sold)        AS units_sold,
    SUM(weight_kg)         AS weight_kg,
    SUM(active_clients)    AS active_clients,
    SUM(transaction_count) AS transactions,
    CASE WHEN SUM(target) > 0
         THEN ROUND(SUM(revenue) / SUM(target) * 100, 2) ELSE NULL
    END AS achievement_pct
FROM v_monthly_kpi
GROUP BY ALL;


-- ---------------------------------------------------------------------------
-- 7. SALESPERSON KPI
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_salesperson_kpi AS
SELECT
    year,
    month,
    region,
    subregion,
    supervisor_name,
    salesperson_id,
    salesperson_name,
    sales_channel,
    SUM(revenue)           AS revenue,
    SUM(target)            AS target,
    SUM(units_sold)        AS units_sold,
    SUM(weight_kg)         AS weight_kg,
    SUM(active_clients)    AS active_clients,
    SUM(transaction_count) AS transactions,
    CASE WHEN SUM(target) > 0
         THEN ROUND(SUM(revenue) / SUM(target) * 100, 2) ELSE NULL
    END AS achievement_pct
FROM v_monthly_kpi
GROUP BY ALL;


-- ---------------------------------------------------------------------------
-- 8. PRODUCT KPI
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_product_kpi AS
SELECT
    sb.sale_year               AS year,
    sb.sale_month              AS month,
    sb.region,
    sb.product_category,
    sb.product_subcategory,
    sb.product_name,
    sb.sku,
    sb.is_innovation_product,
    SUM(sb.total_amount)       AS revenue,
    SUM(sb.quantity)           AS units_sold,
    SUM(sb.total_weight_kg)    AS weight_kg,
    COUNT(DISTINCT sb.clientsd_id) AS active_clients,
    AVG(sb.price_variance_pct) AS avg_price_variance_pct
FROM v_sales_base sb
GROUP BY ALL;


-- ---------------------------------------------------------------------------
-- 9. INNOVATION KPI
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_innovation_kpi AS
SELECT
    sale_year               AS year,
    sale_month              AS month,
    region,
    product_category,
    is_innovation_product,
    SUM(total_amount)       AS revenue,
    SUM(quantity)           AS units_sold,
    COUNT(DISTINCT clientsd_id) AS active_clients
FROM v_sales_base
GROUP BY ALL;


-- ---------------------------------------------------------------------------
-- 10. YEAR-OVER-YEAR
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_yoy_comparison AS
WITH base AS (
    SELECT
        year, month, region, subregion,
        salesperson_id, salesperson_name, supervisor_name, product_category,
        SUM(revenue)    AS revenue,
        SUM(target)     AS target,
        SUM(units_sold) AS units_sold
    FROM v_monthly_kpi
    GROUP BY ALL
)
SELECT
    cy.year, cy.month, cy.region, cy.subregion,
    cy.salesperson_id, cy.salesperson_name, cy.supervisor_name, cy.product_category,
    cy.revenue              AS current_revenue,
    cy.target               AS current_target,
    cy.units_sold           AS current_units,
    py.revenue              AS prior_year_revenue,
    py.units_sold           AS prior_year_units,
    CASE WHEN COALESCE(py.revenue, 0) > 0
         THEN ROUND((cy.revenue - py.revenue) / py.revenue * 100, 2) ELSE NULL
    END AS revenue_yoy_pct,
    CASE WHEN COALESCE(py.units_sold, 0) > 0
         THEN ROUND((cy.units_sold - py.units_sold) / py.units_sold * 100, 2) ELSE NULL
    END AS units_yoy_pct
FROM base cy
LEFT JOIN base py
    ON  py.year             = cy.year - 1
    AND py.month            = cy.month
    AND py.region           = cy.region
    AND py.salesperson_id   = cy.salesperson_id
    AND py.product_category = cy.product_category;


-- ---------------------------------------------------------------------------
-- 11. QUARTERLY KPI — revenue + target joined via v_monthly_kpi
-- Targets are stored monthly: we map months → quarters here so the view
-- is the single place that owns that logic.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_quarterly_kpi AS
WITH quarterly_sales AS (
    -- Actual sales aggregated to quarter grain via dim_date
    SELECT
        sb.sale_year               AS year,
        dd.quarter,
        sb.region,
        sb.subregion,
        sb.supervisor_name,
        sb.salesperson_id,
        sb.salesperson_name,
        sb.product_category,
        SUM(sb.total_amount)           AS revenue,
        SUM(sb.quantity)               AS units_sold,
        SUM(sb.total_weight_kg)        AS weight_kg,
        COUNT(DISTINCT sb.clientsd_id) AS active_clients
    FROM v_sales_base sb
    LEFT JOIN bi.dim_date dd ON sb.sale_date = dd.date_actual
    GROUP BY ALL
),
quarterly_targets AS (
    -- Targets are monthly: sum them into quarters using CEIL(month / 3.0)
    SELECT
        target_year                        AS year,
        CEIL(target_month_num / 3.0)::INT  AS quarter,
        region,
        subregion,
        supervisor_name,
        salesperson_id,
        salesperson_name,
        product_category,
        SUM(target_amount)                 AS target
    FROM v_targets_base
    GROUP BY ALL
)
SELECT
    COALESCE(s.year,             t.year)             AS year,
    COALESCE(s.quarter,          t.quarter)          AS quarter,
    COALESCE(s.region,           t.region)           AS region,
    COALESCE(s.subregion,        t.subregion)        AS subregion,
    COALESCE(s.supervisor_name,  t.supervisor_name)  AS supervisor_name,
    COALESCE(s.salesperson_id,   t.salesperson_id)   AS salesperson_id,
    COALESCE(s.salesperson_name, t.salesperson_name) AS salesperson_name,
    COALESCE(s.product_category, t.product_category) AS product_category,
    COALESCE(s.revenue,      0)  AS revenue,
    COALESCE(t.target,       0)  AS target,
    COALESCE(s.units_sold,   0)  AS units_sold,
    COALESCE(s.weight_kg,    0)  AS weight_kg,
    COALESCE(s.active_clients, 0) AS active_clients,
    CASE
        WHEN COALESCE(t.target, 0) > 0
        THEN ROUND(COALESCE(s.revenue, 0) / t.target * 100, 2)
        ELSE NULL
    END AS achievement_pct
FROM quarterly_sales s
FULL OUTER JOIN quarterly_targets t
    -- FIX: region removed from the join key, matching v_monthly_kpi's
    -- documented design (targets are salesperson-keyed — joining on region
    -- orphans a salesperson's target rows whenever an SCD change moves them
    -- to a different region than the one denormalized on their sales).
    ON  s.year             = t.year
    AND s.quarter          = t.quarter
    AND s.salesperson_id   = t.salesperson_id
    AND s.product_category = t.product_category;


-- ---------------------------------------------------------------------------
-- 12. EXECUTIVE SUMMARY — single row per period
-- NOTE: total_active_clients sums per-salesperson/category distinct counts
-- (upper bound). The reporting layer queries v_sales_base for a true
-- distinct client count instead.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_executive_summary AS
SELECT
    year,
    month,
    SUM(revenue)           AS total_revenue,
    SUM(target)            AS total_target,
    CASE WHEN SUM(target) > 0
         THEN ROUND(SUM(revenue) / SUM(target) * 100, 2) ELSE NULL
    END AS achievement_pct,
    SUM(units_sold)        AS total_units,
    SUM(weight_kg)         AS total_weight_kg,
    SUM(active_clients)    AS total_active_clients,
    SUM(transaction_count) AS total_transactions,
    COUNT(DISTINCT region) AS active_regions,
    COUNT(DISTINCT salesperson_id) AS active_salespeople
FROM v_monthly_kpi
GROUP BY year, month;


-- ---------------------------------------------------------------------------
-- 13. CLIENT KPI
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_client_kpi AS
SELECT
    sb.sale_year               AS year,
    sb.sale_month              AS month,
    sb.region,
    sb.subregion,
    sb.client_city             AS city,
    sb.clientsd_id,
    sb.client_name,
    sb.is_destocked,
    SUM(sb.total_amount)       AS revenue,
    SUM(sb.quantity)           AS units_sold,
    SUM(sb.total_weight_kg)    AS weight_kg,
    COUNT(DISTINCT sb.sku)     AS distinct_skus,
    COUNT(sb.sales_line_id)    AS transaction_count
FROM v_sales_base sb
GROUP BY ALL;


-- ---------------------------------------------------------------------------
-- 14. KP SELL-IN / SELL-OUT KPIs
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
