-- sqlmesh/models/reports/rep_top_products.sql

MODEL (
  name reports.rep_top_products,
  kind FULL,
  owner analytics_team,
  cron '@daily',
  storage_format 'parquet',
  description 'Top 20 products by revenue with 30-day trend analysis'
);

WITH last_30_days AS (
  SELECT
    f.sku,
    p.product_name,
    p.product_category,
    p.product_subcategory,
    p.is_innovation_product,
    COUNT(DISTINCT f.sales_line_id) AS transactions,
    SUM(f.quantity) AS total_quantity_sold,
    SUM(f.sales_amount) AS total_revenue,
    COUNT(DISTINCT f.salesperson_key) AS salespeople_selling,
    COUNT(DISTINCT f.client_key) AS unique_customers,
    COUNT(DISTINCT f.region) AS regions_sold_in,
    AVG(f.unit_price) AS avg_unit_price
  FROM facts.fact_sales f
  JOIN marts.dim_product p 
    ON f.product_key = p.product_key
  WHERE f.sale_date >= CURRENT_DATE - INTERVAL 30 DAYS
  GROUP BY 1, 2, 3, 4, 5
),

last_60_to_30_days AS (
  SELECT
    f.sku,
    SUM(f.sales_amount) AS prev_period_revenue
  FROM marts.fact_sales f
  WHERE f.sale_date >= CURRENT_DATE - INTERVAL 60 DAYS
    AND f.sale_date < CURRENT_DATE - INTERVAL 30 DAYS
  GROUP BY 1
)

SELECT
  curr.sku,
  curr.product_name,
  curr.product_category,
  curr.product_subcategory,
  curr.is_innovation_product,
  
  -- Current period metrics
  curr.total_revenue,
  curr.total_quantity_sold,
  curr.transactions,
  curr.unique_customers,
  curr.salespeople_selling,
  curr.regions_sold_in,
  curr.avg_unit_price,
  ROUND(curr.total_revenue / NULLIF(curr.transactions, 0), 0) AS avg_transaction_value,
  
  -- Trend analysis
  COALESCE(prev.prev_period_revenue, 0) AS prev_period_revenue,
  curr.total_revenue - COALESCE(prev.prev_period_revenue, 0) AS revenue_change,
  ROUND(
    (curr.total_revenue - COALESCE(prev.prev_period_revenue, 0)) * 100.0 / 
    NULLIF(prev.prev_period_revenue, 0),
    1
  ) AS revenue_growth_pct,
  
  -- Trend classification
  CASE
    WHEN prev.prev_period_revenue IS NULL THEN '🆕 New'
    WHEN curr.total_revenue > prev.prev_period_revenue * 1.2 THEN '🚀 Hot'
    WHEN curr.total_revenue > prev.prev_period_revenue THEN '📈 Growing'
    WHEN curr.total_revenue >= prev.prev_period_revenue * 0.9 THEN '➡️ Stable'
    ELSE '📉 Declining'
  END AS trend,
  
  ROW_NUMBER() OVER (ORDER BY curr.total_revenue DESC) AS revenue_rank,
  CURRENT_TIMESTAMP AS generated_at

FROM last_30_days curr
LEFT JOIN last_60_to_30_days prev ON curr.sku = prev.sku
ORDER BY curr.total_revenue DESC
LIMIT 20;