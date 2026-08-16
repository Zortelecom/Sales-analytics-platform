/*
  Revenue vs target by month, salesperson and category.

  Ported from serving/templates/bi_views.sql.

  WHY IT MOVED
  ────────────
  These views were raw SQL applied to serving.db AFTER each sync, so SQLMesh
  knew nothing about them. Two consequences showed up immediately on inspection:

    * v_sales_base still selected fact_sales.unit_price_actual, a column the
      pricing rework renamed. Nothing caught it -- it would have surfaced as a
      broken dashboard.
    * v_product_kpi and v_innovation_kpi were each defined TWICE in the script.
      The later definition silently won (it added subregion); the earlier one
      was dead code nobody could see.

  As SQLMesh models both are impossible: a renamed upstream column fails at
  plan time, and two models with the same name is a hard error.

  Materialised as VIEW: no storage, always current with the marts underneath,
  and every consumer -- Streamlit, Superset, Metabase, the Parquet publish --
  reads the same definition instead of a per-tool copy.
*/
MODEL (
  name bi.v_monthly_kpi,
  kind VIEW,
  owner analytics_team,
  cron '@daily',
  description 'Revenue vs target by month, salesperson and category'
);

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
    FROM bi.v_sales_base
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
    FROM bi.v_targets_base
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
-- distinct YTD clients). Query bi.v_sales_base directly for a true distinct.
-- ---------------------------------------------------------------------------;
