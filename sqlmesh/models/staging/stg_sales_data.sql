MODEL (
  name staging.stg_sales_data,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column sale_date
    lookback 7
  ),
  cron '@daily',
  grain (sales_line_id),
  owner analytics_team,
  storage_format 'parquet'
);

SELECT
  -- Primary key
  sales_line_id,
  
  -- Date parsing
  TRY_CAST(date AS DATE) AS sale_date,
  
  -- Product identifiers
  TRIM(UPPER(sku)) AS sku,
  TRIM(product_name) AS product_name,
  TRIM(product_cat) AS product_category,
  TRIM(product_subcat) AS product_subcategory,
  
  -- Measures
  TRY_CAST(NULLIF(TRIM(qty), '') AS DECIMAL(10, 2)) AS quantity,
  TRY_CAST(NULLIF(TRIM(unit_price), '') AS INTEGER) AS unit_price,
  TRY_CAST(NULLIF(TRIM(amount), '') AS DECIMAL(12,2)) AS sales_amount,
  TRY_CAST(NULLIF(TRIM(unit_weight), '') AS DECIMAL(10,2)) AS unit_weight_kg,
  
  -- Salesperson identifiers
  TRIM(salesperson_id) AS salesperson_id,
  TRIM(salesperson) AS salesperson_name,
  TRIM(supervisor) AS supervisor_name,
  TRIM(channel) AS sales_channel,
  
  -- Client identifiers
  TRIM(clientsd_id) AS client_id,
  TRIM(sd_destocke) AS sd_destocked,
  
  -- Geographic attributes
  TRIM(region) AS region,
  TRIM(subregion) AS subregion,
  TRIM(city) AS city,
  TRIM(localisation) AS location,
  TRIM(filename_subregion) AS filename_subregion,
  
  -- Product flags
  CASE 
    WHEN LOWER(TRIM(is_innovation)) IN ('true', 'yes', '1', 'oui') THEN TRUE
    ELSE FALSE
  END AS is_innovation_product

FROM raw.sales_data
WHERE TRY_CAST(date AS DATE) IS NOT NULL
  AND TRY_CAST(date AS DATE) >= @start_date
  AND TRY_CAST(date AS DATE) < @end_date
  AND TRIM(sku) IS NOT NULL
  AND TRIM(sku) != '';
