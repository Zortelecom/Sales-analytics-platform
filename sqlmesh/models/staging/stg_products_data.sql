MODEL (
  name staging.stg_products_data,
  kind FULL,
  cron '@daily',
  grain (sku),
  owner analytics_team,
  storage_format 'parquet'
);

SELECT
  product_ref_id,
  TRIM(UPPER(sku)) AS sku,
  TRIM(product_name) AS product_name,
  TRIM(product_category) AS product_category,
  TRIM(product_subcategory) AS product_subcategory,
  TRY_CAST(NULLIF(unit_price, "") AS INTEGER) AS unit_price,
  TRY_CAST(NULLIF(unit_weight, "") AS DECIMAL(10,2)) AS unit_weight_kg,
  CASE 
    WHEN LOWER(TRIM(is_innovation)) IN ('true', 'yes', '1', 'oui') THEN TRUE
    ELSE FALSE
  END AS is_innovation_product

FROM raw.products_data
WHERE TRIM(sku) IS NOT NULL 
  AND TRIM(sku) != '';
