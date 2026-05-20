-- sqlmesh/models/reports/rep_target_attainment.sql

MODEL (
  name reports.rep_target_attainment,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column performance_month
  ),
  owner analytics_team,
  cron '@daily',
  storage_format 'parquet',
  description 'Target achievement analysis by salesperson and product category'
);

WITH monthly_sales AS (
  SELECT
    DATE_TRUNC('month', f.sale_date) AS performance_month,
    f.salesperson_id,
    f.salesperson_name,
    f.region,
    f.subregion,
    f.sales_channel,
    f.product_category,
    SUM(f.total_amount) AS actual_sales,
    SUM(f.quantity) AS total_quantity
  FROM marts.fact_sales f
  WHERE f.sale_date >= @start_date 
    AND f.sale_date < @end_date
  GROUP BY 1, 2, 3, 4, 5, 6, 7
),

monthly_targets AS (
  SELECT
    t.target_month AS performance_month,
    t.salesperson_id,
    t.product_category,
    SUM(t.target_amount) AS target_amount
  FROM marts.fact_targets t
  GROUP BY 1, 2, 3
)

SELECT
  s.performance_month,
  EXTRACT(YEAR FROM s.performance_month) AS year,
  EXTRACT(MONTH FROM s.performance_month) AS month,
  
  -- Salesperson details
  s.salesperson_id,
  s.salesperson_name,
  s.region,
  s.subregion,
  s.sales_channel,
  
  -- Category
  s.product_category,
  
  -- Performance metrics
  s.actual_sales,
  s.total_quantity,
  COALESCE(t.target_amount, 0) AS target_amount,
  
  -- Variance analysis
  s.actual_sales - COALESCE(t.target_amount, 0) AS variance_amount,
  CASE 
    WHEN t.target_amount > 0 
    THEN ROUND((s.actual_sales / t.target_amount) * 100, 1)
    ELSE NULL
  END AS achievement_pct,
  
  -- Performance tier
  CASE
    WHEN t.target_amount IS NULL THEN 'No Target Set'
    WHEN s.actual_sales >= t.target_amount * 1.15 THEN '⭐ Excellent (115%+)'
    WHEN s.actual_sales >= t.target_amount * 1.05 THEN '✅ Exceeds (105-115%)'
    WHEN s.actual_sales >= t.target_amount * 0.95 THEN '🟢 Meets (95-105%)'
    WHEN s.actual_sales >= t.target_amount * 0.85 THEN '🟡 Near (85-95%)'
    WHEN s.actual_sales >= t.target_amount * 0.70 THEN '🟠 Below (70-85%)'
    ELSE '🔴 Critical (<70%)'
  END AS performance_tier,
  
  
  CURRENT_TIMESTAMP AS generated_at

FROM monthly_sales s
LEFT JOIN monthly_targets t
  ON s.performance_month = t.performance_month
  AND s.salesperson_id = t.salesperson_id
  AND s.product_category = t.product_category
ORDER BY s.performance_month DESC, s.actual_sales DESC;