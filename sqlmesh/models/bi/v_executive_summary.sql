/*
  Single-row period summary.

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
  name bi.v_executive_summary,
  kind VIEW,
  owner analytics_team,
  cron '@daily',
  description 'Single-row period summary'
);

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
FROM bi.v_monthly_kpi
GROUP BY year, month;


-- ---------------------------------------------------------------------------
-- 13. CLIENT KPI
-- ---------------------------------------------------------------------------;
