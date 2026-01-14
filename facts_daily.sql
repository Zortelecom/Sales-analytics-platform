MODEL (
  name sales_analytics.fact_sales_daily,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column sale_date
  ),
  cron '@daily',
  grain (date_key, product_key, salesperson_key),
  physical_properties (
    location 's3://sales-analytics-lake/facts/fact_sales_daily',
    file_format 'parquet',
    partitioned_by ['sale_year', 'sale_month']
  )
);

SELECT
  -- Dimension keys
  date_key,
  sale_date,
  sale_year,
  sale_month,
  product_key,
  sku,
  salesperson_key,
  salesperson_id,
  geography_key,
  
  -- AGGREGATED MEASURES
  SUM(quantity) AS total_quantity,
  SUM(sales_amount) AS total_sales_amount,
  SUM(total_weight_kg) AS total_weight_kg,
  COUNT(sales_line_id) AS transaction_count,
  
  -- AVERAGE MEASURES (semi-additive)
  AVG(unit_price) AS avg_unit_price,
  AVG(data_quality_score) AS avg_quality_score,
  
  -- MIN/MAX MEASURES
  MIN(unit_price) AS min_unit_price,
  MAX(unit_price) AS max_unit_price,
  
  -- DISTINCT COUNTS (non-additive)
  COUNT(DISTINCT client_key) AS unique_clients

FROM sales_analytics.fact_sales
WHERE sale_date >= @start_date AND sale_date < @end_date
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9;