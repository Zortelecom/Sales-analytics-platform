/*
  Year-to-date rollup of v_monthly_kpi.

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
  name bi.v_ytd_kpi,
  kind VIEW,
  owner analytics_team,
  cron '@daily',
  description 'Year-to-date rollup of v_monthly_kpi'
);

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
FROM bi.v_monthly_kpi
GROUP BY ALL;


-- ---------------------------------------------------------------------------
-- 5. WEEKLY KPI
-- ---------------------------------------------------------------------------;
