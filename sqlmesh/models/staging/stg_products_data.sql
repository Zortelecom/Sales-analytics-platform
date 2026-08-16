/*
  Product attributes.

  (2026-08) unit_price is NO LONGER REQUIRED to be non-null.

  It is the traditional-trade tier price, and some products are not sold in
  traditional trade at all -- they exist only in GMS or only as sell-in. The
  old not_null and accepted_range(min_v := 1) on this column would fail the
  whole model for a legitimately GMS-only product.

  What replaces it is priced_tier_count and
  assert_product_has_at_least_one_price: a product priced in SOME tier is
  fine, a product priced in NONE is a master-data gap. That is the real
  constraint, and it is the one that was missing.

  unit_price stays here because dim_products and the Power BI model reference
  it. The per-tier prices live in staging.stg_product_prices, which is
  authoritative when the two ever disagree.
*/
MODEL (
  name staging.stg_products_data,
  kind FULL,
  cron '@daily',
  grain (sku, effective_from),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    unique_combination_of_columns(columns := (sku, effective_from)),

    -- unit_price is deliberately absent: see the header.
    not_null(columns := (
      sku,
      product_name,
      product_category,
      effective_from
    )),

    -- A price that is PRESENT must still be positive. NULL passes, which is
    -- the point -- absence is handled by the tier-coverage audit below.
    accepted_range(column := unit_price, min_v := 1, inclusive := true),
    accepted_range(column := unit_weight_kg, min_v := 0, inclusive := true),

    -- A product must be priced in at least one channel.
    assert_product_has_at_least_one_price
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
    WHEN LOWER(TRIM(CAST(is_innovation AS VARCHAR)))
         IN ('true', 'yes', '1', 'oui') THEN TRUE
    ELSE FALSE
  END                                                         AS is_innovation_product,

  -- How many channels this product version can be valued in. Counted from the
  -- source columns rather than joined from stg_product_prices, because a
  -- product with no price at all has no rows there to join to -- which is
  -- exactly the case being detected.
  --
  -- Must stay in step with the enabled tiers in stg_product_prices.sql: while
  -- this counted TT alone, two genuinely-priced products (Mambo Prestige Noir,
  -- Le Bon Patissier -- GMS and SD only) counted 0 and failed the audit.
  (
    CASE WHEN TRY_CAST(unit_price     AS DECIMAL(12,2)) IS NOT NULL THEN 1 ELSE 0 END
  + CASE WHEN TRY_CAST(unit_price_gms AS DECIMAL(12,2)) IS NOT NULL THEN 1 ELSE 0 END
  + CASE WHEN TRY_CAST(unit_price_sd  AS DECIMAL(12,2)) IS NOT NULL THEN 1 ELSE 0 END
  )                                                           AS priced_tier_count,

  TRY_CAST(effective_from AS TIMESTAMP)                       AS effective_from

FROM raw.products_data
WHERE TRIM(sku) IS NOT NULL
  AND TRIM(sku) != ''
  -- A row that cannot be placed in time cannot be historized downstream.
  AND TRY_CAST(effective_from AS TIMESTAMP) IS NOT NULL;