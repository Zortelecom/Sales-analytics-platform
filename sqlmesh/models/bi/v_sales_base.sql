/*
  Denormalised sell-out lines with date, product and client attributes.

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
  name bi.v_sales_base,
  kind VIEW,
  owner analytics_team,
  cron '@daily',
  description 'Denormalised sell-out lines with date, product and client attributes'
);

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
    fs.unit_price_effective,
    fs.unit_price_standard,
    fs.total_amount,
    fs.unit_weight_actual,
    fs.unit_weight_standard,
    fs.total_weight_kg,
    fs.price_variance_pct

FROM marts.fact_sales fs
LEFT JOIN marts.dim_date        dd ON fs.date_key        = dd.date_key
-- SCD Type 2 guard: match the exact dimension version valid at sale time
LEFT JOIN marts.dim_products    dp ON fs.product_key     = dp.product_key
                                AND (fs.sale_date >= dp.valid_from AND (dp.valid_to IS NULL OR fs.sale_date < dp.valid_to))
-- dim_salesperson join removed: all salesperson fields (salesperson_name, region,
-- subregion, sales_channel, supervisor_name) are already denormalized into fact_sales.
-- The join added no columns but caused row fan-out on SCD Type 2 history rows.
LEFT JOIN marts.dim_clientsd    dc ON fs.clientsd_key    = dc.clientsd_key
                                AND (fs.sale_date >= dc.valid_from AND (dc.valid_to IS NULL OR fs.sale_date < dc.valid_to));


-- ---------------------------------------------------------------------------
-- 2. TARGET BASE VIEW
-- ---------------------------------------------------------------------------;
