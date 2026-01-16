MODEL (
  name marts.dim_products,
  kind FULL,
  cron '@daily',
  grain (product_key),
  owner analytics_team,
  storage_format 'parquet'
);


SELECT
  product_key,
  sku,
  product_name,
  product_category,
  product_subcategory,
  unit_price,
  unit_weight_kg,
  is_innovation_product,
  valid_from,
  valid_to
FROM staging.stg_products_data
