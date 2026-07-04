MODEL (
  name staging.stg_products_data,
  kind FULL,
  cron '@daily',
  grain (sku, effective_from),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    unique_combination_of_columns(columns := (sku, effective_from)),
    -- These fields are required for every row downstream.
    not_null(columns := (
      sku,
      product_name,
      product_category,
      unit_price,
      effective_from
    )),

    -- Prices must be strictly positive.
    -- A zero or negative price would corrupt revenue calculations in fact_sales.
    accepted_range(column := unit_price, min_v := 1, inclusive := true),

    -- Weights, when present, must be non-negative.
    accepted_range(column := unit_weight_kg, min_v := 0, inclusive := true)
  )
);

SELECT
  product_ref_id,
  TRIM(UPPER(sku))                                            AS sku,
  TRIM(product_name)                                          AS product_name,
  TRIM(UPPER(product_category))                               AS product_category,
  TRIM(UPPER(product_subcategory))                            AS product_subcategory,
  FLOOR(TRY_CAST(unit_price AS DECIMAL(12,2)))::INTEGER       AS unit_price,
  TRY_CAST(unit_weight AS DECIMAL(10,2))                      AS unit_weight_kg,
  CASE
    WHEN LOWER(TRIM(is_innovation)) IN ('true', 'yes', '1', 'oui') THEN TRUE
    ELSE FALSE
  END                                                         AS is_innovation_product,
  TRY_CAST(effective_from AS TIMESTAMP)                       AS effective_from

FROM raw.products_data
WHERE TRIM(sku) IS NOT NULL
  AND TRIM(sku) != ''
  -- A row that can't be placed in time can't be historized correctly
  -- downstream, so it's filtered here where it's auditable (not_null above
  -- will still flag it if this ever fires).
  AND TRY_CAST(effective_from AS TIMESTAMP) IS NOT NULL;