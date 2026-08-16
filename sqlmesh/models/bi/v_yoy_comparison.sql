/*
  Year-over-year delta by month and salesperson.

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
  name bi.v_yoy_comparison,
  kind VIEW,
  owner analytics_team,
  cron '@daily',
  description 'Year-over-year delta by month and salesperson'
);

WITH base AS (
    SELECT
        year, month, region, subregion,
        salesperson_id, salesperson_name, supervisor_name, product_category,
        SUM(revenue)    AS revenue,
        SUM(target)     AS target,
        SUM(units_sold) AS units_sold
    FROM bi.v_monthly_kpi
    GROUP BY ALL
)
SELECT
    cy.year, cy.month, cy.region, cy.subregion,
    cy.salesperson_id, cy.salesperson_name, cy.supervisor_name, cy.product_category,
    cy.revenue              AS current_revenue,
    cy.target               AS current_target,
    cy.units_sold           AS current_units,
    py.revenue              AS prior_year_revenue,
    py.units_sold           AS prior_year_units,
    CASE WHEN COALESCE(py.revenue, 0) > 0
         THEN ROUND((cy.revenue - py.revenue) / py.revenue * 100, 2) ELSE NULL
    END AS revenue_yoy_pct,
    CASE WHEN COALESCE(py.units_sold, 0) > 0
         THEN ROUND((cy.units_sold - py.units_sold) / py.units_sold * 100, 2) ELSE NULL
    END AS units_yoy_pct
FROM base cy
LEFT JOIN base py
    ON  py.year             = cy.year - 1
    AND py.month            = cy.month
    AND py.region           = cy.region
    AND py.salesperson_id   = cy.salesperson_id
    AND py.product_category = cy.product_category;


-- ---------------------------------------------------------------------------
-- 11. QUARTERLY KPI — revenue + target joined via bi.v_monthly_kpi
-- Targets are stored monthly: we map months → quarters here so the view
-- is the single place that owns that logic.
-- ---------------------------------------------------------------------------;
