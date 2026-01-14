MODEL (
  name reports.rep_weekly_meeting,
  kind FULL,
  owner analytics_team,
  cron '0 8 * * MON',  -- Run every Monday at 8 AM
  storage_format 'parquet',
  description 'Weekly sales performance snapshot for management meetings'
);

WITH date_context AS (
  SELECT
    DATE_TRUNC('week', CURRENT_DATE) AS current_week_start,
    DATE_TRUNC('week', CURRENT_DATE - INTERVAL 7 DAYS) AS last_week_start,
    DATE_TRUNC('month', CURRENT_DATE) AS current_month_start
),

this_week_sales AS (
  SELECT
    f.salesperson_id,
    sp.salesperson_name,
    sp.region,
    sp.subregion,
    sp.sales_channel,
    COUNT(DISTINCT f.sales_line_id) AS transactions_count,
    SUM(f.quantity) AS total_quantity,
    SUM(f.sales_amount) AS total_sales,
    COUNT(DISTINCT f.client_key) AS unique_customers,
    COUNT(DISTINCT f.product_key) AS unique_products
  FROM marts.fact_sales f
  CROSS JOIN date_context dc
  JOIN marts.dim_salesperson sp 
    ON f.salesperson_key = sp.salesperson_key
  WHERE f.sale_date >= dc.current_week_start
    AND f.sale_date < dc.current_week_start + INTERVAL 7 DAYS
  GROUP BY 1, 2, 3, 4, 5
),

last_week_sales AS (
  SELECT
    f.salesperson_id,
    SUM(f.sales_amount) AS last_week_sales
  FROM sales_lakehouse.facts.fact_sales f
  CROSS JOIN date_context dc
  WHERE f.sale_date >= dc.last_week_start
    AND f.sale_date < dc.last_week_start + INTERVAL 7 DAYS
  GROUP BY 1
),

mtd_sales AS (
  SELECT
    f.salesperson_id,
    SUM(f.sales_amount) AS mtd_sales
  FROM marts.fact_sales f
  CROSS JOIN date_context dc
  WHERE f.sale_date >= dc.current_month_start
  GROUP BY 1
),

monthly_targets AS (
  SELECT
    t.salesperson_id,
    SUM(t.target_amount) AS monthly_target
  FROM marts.fact_targets t
  CROSS JOIN date_context dc
  WHERE t.target_month = dc.current_month_start
  GROUP BY 1
)

SELECT
  tw.salesperson_id,
  tw.salesperson_name,
  tw.region,
  tw.subregion,
  tw.sales_channel,
  
  -- This week performance
  tw.total_sales AS this_week_sales,
  tw.transactions_count AS this_week_transactions,
  tw.unique_customers AS this_week_customers,
  tw.unique_products AS this_week_products,
  ROUND(tw.total_sales / NULLIF(tw.transactions_count, 0), 0) AS avg_transaction_value,
  
  -- Week-over-week comparison
  COALESCE(lw.last_week_sales, 0) AS last_week_sales,
  tw.total_sales - COALESCE(lw.last_week_sales, 0) AS wow_change,
  ROUND(
    (tw.total_sales - COALESCE(lw.last_week_sales, 0)) * 100.0 / 
    NULLIF(lw.last_week_sales, 0), 
    1
  ) AS wow_growth_pct,
  
  -- Month-to-date performance
  COALESCE(mt.mtd_sales, 0) AS mtd_sales,
  COALESCE(tg.monthly_target, 0) AS monthly_target,
  ROUND(
    COALESCE(mt.mtd_sales, 0) * 100.0 / NULLIF(tg.monthly_target, 0),
    1
  ) AS target_attainment_pct,
  tg.monthly_target - COALESCE(mt.mtd_sales, 0) AS target_gap,
  
  -- Status indicator
  CASE
    WHEN mt.mtd_sales >= tg.monthly_target THEN '✅ Target Met'
    WHEN mt.mtd_sales >= tg.monthly_target * 0.9 THEN '🟡 On Track'
    WHEN mt.mtd_sales >= tg.monthly_target * 0.7 THEN '🟠 Needs Attention'
    ELSE '🔴 At Risk'
  END AS status,
  
  (SELECT current_week_start FROM date_context) AS report_week,
  CURRENT_TIMESTAMP AS generated_at

FROM this_week_sales tw
LEFT JOIN last_week_sales lw ON tw.salesperson_id = lw.salesperson_id
LEFT JOIN mtd_sales mt ON tw.salesperson_id = mt.salesperson_id
LEFT JOIN monthly_targets tg ON tw.salesperson_id = tg.salesperson_id
ORDER BY tw.total_sales DESC;