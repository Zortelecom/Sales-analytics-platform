MODEL (
  name marts.fact_sales,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column sale_date
  ),
  cron '@daily',
  grain (sales_line_id),
  owner analytics_team,
  storage_format 'parquet',
  partitioned_by (sale_year, sale_month)
);

SELECT
  -- Primary key
  s.sales_line_id,
  
  -- Date dimension FK
  CAST(STRFTIME(s.sale_date, '%Y%m%d') AS INTEGER) AS date_key,
  s.sale_date,
  EXTRACT(YEAR FROM s.sale_date) AS sale_year,
  EXTRACT(MONTH FROM s.sale_date) AS sale_month,
  
  -- Product dimension FK (SCD Type 2 join)
  p.product_key,
  p.sku AS product_sku,
  p.product_name,
  p.product_category,
  p.product_subcategory,
  p.is_innovation_product,
  
  -- Salesperson dimension FK (SCD Type 2 join)
  sp.salesperson_key,
  sp.salesperson_id,
  sp.salesperson_name,
  sp.region AS salesperson_region,
  sp.subregion AS salesperson_subregion,
  sp.sales_channel,
  sp.supervisor_name,
  
  -- Client dimension FK (SCD Type 2 join)
  c.sd_id AS customer_id,
  c.sd_name AS customer_name,
  c.region AS customer_region,
  c.subregion AS customer_subregion,
  c.city AS customer_city,
  c.key_player AS key_player,
  c.is_destocked AS is_customer_destocked,
  
  -- MEASURES
  s.quantity,
  s.unit_price AS unit_price_actual,
  s.sales_amount AS total_amount,
  
  -- Use dimension price for comparison
  p.unit_price AS unit_price_standard,
  s.unit_weight_kg AS unit_weight_actual,
  p.unit_weight_kg AS unit_weight_standard,
  
  -- Calculated measures
  s.quantity * COALESCE(p.unit_weight_kg, s.unit_weight_kg, 0) AS total_weight_kg,
  
  -- Price variance
  CASE 
    WHEN p.unit_price > 0 AND s.unit_price > 0
    THEN ROUND(((s.unit_price - p.unit_price) / p.unit_price) * 100, 2)
    ELSE 0
  END AS price_variance_pct,
  
  -- Degenerate dimensions
  s.location

FROM staging.stg_sales_data s

-- Product dimension (SCD Type 2 join)
LEFT JOIN marts.dim_products p
  ON s.sku = p.sku
  AND s.sale_date >= p.valid_from
  AND (s.sale_date < p.valid_to OR p.valid_to IS NULL)

-- Salesperson dimension (SCD Type 2 join)
LEFT JOIN marts.dim_salesperson sp
  ON s.salesperson_id = sp.salesperson_id
  AND s.sale_date >= sp.valid_from
  AND (s.sale_date < sp.valid_to OR sp.valid_to IS NULL)

-- Client dimension (SCD Type 2 join)
LEFT JOIN marts.dim_clientsd c
  ON s.client_id = c.sd_id
  AND s.sale_date >= c.valid_from
  AND (s.sale_date < c.valid_to OR c.valid_to IS NULL)

WHERE s.sale_date >= @start_date 
  AND s.sale_date < @end_date;