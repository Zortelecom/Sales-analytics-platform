/*
  Quarterly revenue vs target.

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
  name bi.v_quarterly_kpi,
  kind VIEW,
  owner analytics_team,
  cron '@daily',
  description 'Quarterly revenue vs target'
);

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
    FROM bi.v_sales_base sb
    LEFT JOIN marts.dim_date dd ON sb.sale_date = dd.date_actual
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
    FROM bi.v_targets_base
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
    -- FIX: region removed from the join key, matching bi.v_monthly_kpi's
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
-- (upper bound). The reporting layer queries bi.v_sales_base for a true
-- distinct client count instead.
-- ---------------------------------------------------------------------------;
