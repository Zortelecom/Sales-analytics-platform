MODEL (
  name marts.dim_products,
  kind SCD_TYPE_2_BY_COLUMN (
    unique_key sku,
    columns (unit_price, unit_weight_kg),
    time_data_type TIMESTAMP
  ),
  cron '@daily',
  grain (product_key),
  owner analytics_team,
  storage_format 'parquet'
);

WITH products_with_versions AS (
  SELECT
    sku,
    product_name,
    product_category,
    product_subcategory,
    unit_price,
    unit_weight_kg,
    is_innovation_product
  FROM staging.stg_products_data
)

SELECT
  -- Surrogate key - Integer for Power BI relationships
  ROW_NUMBER() OVER (ORDER BY sku, CURRENT_TIMESTAMP) AS product_key,
  
  -- Natural key (business key)
  sku,
  
  -- Product attributes
  product_name,
  product_category,
  product_subcategory,
  unit_price,
  unit_weight_kg,
  is_innovation_product
  
  -- Note: valid_from and valid_to are added automatically by SCD_TYPE_2
FROM products_with_versions;