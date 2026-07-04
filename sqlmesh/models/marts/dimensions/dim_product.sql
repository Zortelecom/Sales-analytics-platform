MODEL (
  name marts.dim_products,
  kind SCD_TYPE_2_BY_TIME (
    unique_key (sku),
    updated_at_name effective_from,
    updated_at_as_valid_from true
  ),
  start '2025-01-01',
  cron '@monthly',
  grain (product_key),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    -- Built-in: primary key integrity.
    unique_values(columns := (product_key)),
    not_null(columns := (product_key, sku)),

    -- Custom: SCD window overlap check — see audits/*.
    -- SQLMesh manages valid_from/valid_to internally for this model kind,
    -- so true overlaps shouldn't occur, but this stays on as a regression
    -- guard now that the historization logic lives here.
    assert_no_overlapping_scd_windows(
        key            := sku,
        surrogate_key  := product_key
    )
  )
);

SELECT
  -- product_key is hashed from (sku, effective_from) rather than the
  -- attribute columns. valid_from (the SQLMesh-managed column) doesn't
  -- exist yet at this point in the query -- it's computed by SQLMesh after
  -- this SELECT runs -- but effective_from is available here and uniquely
  -- identifies each version. This also avoids the attribute-collision bug:
  -- if a product's price or weight ever reverts to a prior value, the two
  -- windows get different keys instead of colliding on the same hash.
  @GENERATE_SURROGATE_KEY(
    TRIM(sku),
    unit_price,
    unit_weight_kg,
    is_innovation_product,
    CAST(effective_from AS TEXT),
    hash_function := 'MD5_NUMBER_LOWER'
  ) AS product_key,

  sku,
  product_name,
  product_category,
  product_subcategory,
  unit_price,
  unit_weight_kg,
  is_innovation_product,
  effective_from

FROM staging.stg_products_data;
