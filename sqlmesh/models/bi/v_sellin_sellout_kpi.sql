/*
  Sell-in vs sell-out by month and SD, with sell-through ratio.

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
  name bi.v_sellin_sellout_kpi,
  kind VIEW,
  owner analytics_team,
  cron '@daily',
  description 'Sell-in vs sell-out by month and SD, with sell-through ratio'
);

WITH sell_in AS (
    SELECT
        year, month, clientsd_id, product_category,
        MAX(region)           AS region,
        MAX(subregion)        AS subregion,
        MAX(supervisor_name)  AS supervisor_name,
        SUM(sell_in_revenue)  AS sell_in_revenue,
        SUM(sell_in_units)    AS sell_in_units
    FROM bi.v_kp_sd_monthly_kpi
    GROUP BY year, month, clientsd_id, product_category
), sell_out AS (
    SELECT
        sale_year AS year, sale_month AS month, clientsd_id, product_category,
        -- bi.v_sales_base carries BOTH the rep's geography (region/subregion)
        -- and the SD's (client_region/client_subregion). Use the SD's here so
        -- the sell-out side is scoped on the same axis as the sell-in side.
        MAX(client_region)    AS region,
        MAX(client_subregion) AS subregion,
        MAX(supervisor_name)  AS supervisor_name,
        SUM(total_amount)     AS sell_out_revenue,
        SUM(quantity)         AS sell_out_units
    FROM bi.v_sales_base
    GROUP BY sale_year, sale_month, clientsd_id, product_category
)
SELECT
    COALESCE(i.year, o.year)                            AS year,
    COALESCE(i.month, o.month)                          AS month,
    COALESCE(i.clientsd_id, o.clientsd_id)              AS clientsd_id,
    COALESCE(i.product_category, o.product_category)    AS product_category,
    COALESCE(i.region, o.region)                        AS region,            -- ADDED
    COALESCE(i.subregion, o.subregion)                  AS subregion,         -- ADDED
    COALESCE(i.supervisor_name, o.supervisor_name)      AS supervisor_name,   -- ADDED
    COALESCE(i.sell_in_revenue, 0)                      AS sell_in_revenue,
    COALESCE(o.sell_out_revenue, 0)                     AS sell_out_revenue,
    COALESCE(i.sell_in_units, 0)                        AS sell_in_units,
    COALESCE(o.sell_out_units, 0)                       AS sell_out_units,
    CASE WHEN COALESCE(i.sell_in_revenue, 0) > 0
         THEN ROUND(COALESCE(o.sell_out_revenue, 0) / i.sell_in_revenue * 100, 2)
    END AS sell_through_pct
FROM sell_in i
FULL OUTER JOIN sell_out o
  ON i.year = o.year AND i.month = o.month AND i.clientsd_id = o.clientsd_id
 AND i.product_category = o.product_category;
