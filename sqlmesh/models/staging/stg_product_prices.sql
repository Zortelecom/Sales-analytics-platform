/*
  Product prices, one row per (sku, price_tier, effective_from).

  ⚠ INTERIM STATE: TT ONLY. See "TO ENABLE THE OTHER TIERS" below.

  WHY THIS MODEL EXISTS
  ─────────────────────
  Ref_Products carries one price per tier as columns, because that is what a
  supervisor can maintain in a spreadsheet. Models want the long form: a fourth
  tier becomes rows rather than a schema change, an audit becomes a join rather
  than a CASE, and each tier's price history moves independently.

  THE TIERS
  ─────────
    TT   traditional trade — salesperson to market. The historical
         Ref_Products.unit_price, unchanged.
    GMS  grande et moyenne surface. The GMS team buys from a Key Player
         treated as an SD, at a different price.
    SD   KP to sub-distributor (sell-in). One step up the chain, so lower
         than TT by construction.

  A product with no price for a tier is ABSENT for that tier rather than
  present with NULL. Downstream that distinguishes "not sold through this
  channel" from "price missing", which are different problems.

  UNION ALL rather than UNPIVOT: both work, but with UNION ALL a tier is one
  contiguous block to enable, instead of a column expression and an UNPIVOT
  entry that have to be kept in sync in two places.

  TO ENABLE THE OTHER TIERS
  ─────────────────────────
    1. Add unit_price_gms and unit_price_sd to Ref_Products. Leave BLANK (not
       zero) where a product is not sold through that channel, and BACK-DATE
       effective_from to match the existing product row -- otherwise every
       historical line joins to a window with no tier price.
    2. contracts.yaml already lists both under ref_products.expected_columns.
    3. Re-run ingestion, then `sqlmesh create_external_models`, and confirm
       external_models.yaml shows the two columns on landing.products_data.
    4. Uncomment the two blocks at the bottom of this file.
*/
MODEL (
  name staging.stg_product_prices,
  kind FULL,
  cron '@daily',
  grain (sku, price_tier, effective_from),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    unique_combination_of_columns(columns := (sku, price_tier, effective_from)),
    not_null(columns := (sku, price_tier, unit_price, effective_from)),
    -- XAF has no subunit; a zero or negative price corrupts every variance.
    accepted_range(column := unit_price, min_v := 1, inclusive := true),
    -- Sell-in must not cost more than traditional trade. Inert until the GMS
    -- and SD tiers are enabled below.
    assert_price_tier_ordering
  )
);

WITH base AS (
  SELECT
    TRIM(UPPER(sku))                      AS sku,
    unit_price                            AS raw_price_tt,
    unit_price_gms                        AS raw_price_gms,
    unit_price_sd                         AS raw_price_sd,
    TRY_CAST(effective_from AS TIMESTAMP) AS effective_from
  FROM raw.products_data
  WHERE TRIM(sku) IS NOT NULL
    AND TRIM(sku) != ''
    AND TRY_CAST(effective_from AS TIMESTAMP) IS NOT NULL
)

SELECT
  sku,
  'TT' AS price_tier,
  FLOOR(TRY_CAST(raw_price_tt AS DECIMAL(12,2)))::INTEGER AS unit_price,
  effective_from
FROM base
WHERE TRY_CAST(raw_price_tt AS DECIMAL(12,2)) IS NOT NULL

UNION ALL
SELECT
  sku,
  'GMS' AS price_tier,
  FLOOR(TRY_CAST(raw_price_gms AS DECIMAL(12,2)))::INTEGER AS unit_price,
  effective_from
FROM base
WHERE TRY_CAST(raw_price_gms AS DECIMAL(12,2)) IS NOT NULL

UNION ALL
SELECT
  sku,
 'SD' AS price_tier,
  FLOOR(TRY_CAST(raw_price_sd AS DECIMAL(12,2)))::INTEGER AS unit_price,
  effective_from
FROM base
WHERE TRY_CAST(raw_price_sd AS DECIMAL(12,2)) IS NOT NULL
;