/*
  Monthly revenue per Key Player across its SDs.

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
  name bi.v_kp_performance_kpi,
  kind VIEW,
  owner analytics_team,
  cron '@daily',
  description 'Monthly revenue per Key Player across its SDs'
);

SELECT
    year, month,
    key_player,                              -- ADDED: the actual grain
    region, subregion, supervisor_name,      -- ADDED: subregion, supervisor
    product_category,
    SUM(sell_in_revenue)                  AS sell_in_revenue,
    SUM(sell_in_units)                    AS sell_in_units,
    SUM(transaction_count)                AS transaction_count,
    SUM(destockage_conflict_count)        AS destockage_conflict_count,
    SUM(kp_mismatch_count)                AS kp_mismatch_count,
    COUNT(DISTINCT clientsd_id)           AS active_sds
FROM bi.v_kp_sd_monthly_kpi
GROUP BY ALL;
