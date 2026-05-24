MODEL (
  name marts.dim_products,
  kind FULL,
  cron '@daily',
  grain (product_key),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    -- Built-in: primary key integrity.
    unique_values(columns := (product_key)),
    not_null(columns := (product_key, sku)),

    -- Custom: SCD window overlap check — see audits/*.
     assert_no_overlapping_scd_windows(
        key            := sku,
        surrogate_key  := product_key
    )
  )
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
