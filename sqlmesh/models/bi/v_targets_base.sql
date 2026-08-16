/*
  Denormalised targets with salesperson attributes.

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
  name bi.v_targets_base,
  kind VIEW,
  owner analytics_team,
  cron '@daily',
  description 'Denormalised targets with salesperson attributes'
);

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
FROM marts.fact_targets ft
LEFT JOIN marts.dim_salesperson ds ON ft.salesperson_key = ds.salesperson_key;


-- ---------------------------------------------------------------------------
-- 3. MONTHLY KPI — core aggregation grain
-- ---------------------------------------------------------------------------
-- region excluded from join key: SCD changes split sales across regions
-- but targets are salesperson-keyed only. Joining on region would orphan
-- targets when a salesperson moves, producing duplicate rows.
-- Known tradeoff: if one salesperson sells in two regions within the same
-- month, both sales rows match the single target row and SUM(target)
-- double-counts downstream. bi.v_quarterly_kpi follows the same rule.
-- ---------------------------------------------------------------------------;
