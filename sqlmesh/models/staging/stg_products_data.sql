MODEL (
  name staging.stg_products_data,
  kind SCD_TYPE_2_BY_COLUMN(
    unique_key (sku),
    columns [unit_price, unit_weight_kg, is_innovation_product]
  ),
  cron '@daily',
  grain (product_key),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    -- Surrogate key must be unique across all rows (including history).
    unique_values(columns := (product_key)),
    -- Core fields required for every row.
    not_null(columns := (
      product_key,
      sku,
      product_name,
      product_category,
      unit_price
    )),

    -- Prices must be strictly positive.
    -- A zero or negative price would corrupt revenue calculations in fact_sales.
    accepted_range(column := unit_price, min_v := 1, inclusive := true),

    -- Weights, when present, must be non-negative.
    accepted_range(column := unit_weight_kg, min_v := 0, inclusive := true)
  )
);

SELECT

  @GENERATE_SURROGATE_KEY (
    TRIM(product_name),
    TRIM(product_category),
    TRIM(product_subcategory),
    TRY_CAST(unit_price AS STRING),
    TRY_CAST(unit_weight AS STRING),
    LOWER(TRIM(is_innovation)),
    hash_function := 'MD5_NUMBER_LOWER'
  ) AS product_key,

  product_ref_id,
  TRIM(UPPER(sku))                        AS sku,
  TRIM(product_name)                      AS product_name,
  TRIM(product_category)                  AS product_category,
  TRIM(product_subcategory)               AS product_subcategory,
  FLOOR(TRY_CAST(unit_price AS DECIMAL(12,2)))::INTEGER        AS unit_price,
  TRY_CAST(unit_weight AS DECIMAL(10,2))  AS unit_weight_kg,
  CASE 
    WHEN LOWER(TRIM(is_innovation)) IN ('true', 'yes', '1', 'oui') THEN TRUE
    ELSE FALSE
  END AS is_innovation_product

FROM raw.products_data
WHERE TRIM(sku) IS NOT NULL 
  AND TRIM(sku) != '';
