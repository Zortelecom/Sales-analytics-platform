/*
  Weekly sell-out activity per salesperson.

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
  name bi.v_weekly_kpi,
  kind VIEW,
  owner analytics_team,
  cron '@daily',
  description 'Weekly sell-out activity per salesperson'
);

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
FROM bi.v_sales_base
GROUP BY ALL;


-- ---------------------------------------------------------------------------
-- 6. REGIONAL KPI
-- ---------------------------------------------------------------------------;
