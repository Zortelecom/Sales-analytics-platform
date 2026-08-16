/*
  Monthly revenue per sub-distributor.

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
  name bi.v_client_kpi,
  kind VIEW,
  owner analytics_team,
  cron '@daily',
  description 'Monthly revenue per sub-distributor'
);

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
FROM bi.v_sales_base sb
GROUP BY ALL;


-- ---------------------------------------------------------------------------
-- 14. KP SELL-IN / SELL-OUT KPIs
-- ---------------------------------------------------------------------------;
