/*
  Sell-in revenue and quantity by month, SD, KP and category.

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
  name bi.v_kp_sd_monthly_kpi,
  kind VIEW,
  owner analytics_team,
  cron '@daily',
  description 'Sell-in revenue and quantity by month, SD, KP and category'
);

SELECT
    sale_year AS year, sale_month AS month,
    region, subregion, supervisor_name, key_player,
    clientsd_id, product_category,
    SUM(total_amount)                 AS sell_in_revenue,
    SUM(quantity)                     AS sell_in_units,
    COUNT(*)                          AS transaction_count,
    COUNT_IF(destockage_channel_conflict) AS destockage_conflict_count,
    COUNT_IF(kp_mismatch)                AS kp_mismatch_count
FROM bi.v_kp_sd_base
GROUP BY ALL;
