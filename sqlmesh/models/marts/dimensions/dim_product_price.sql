/*
  Price dimension: SCD Type 2 per (sku, price_tier).

  Windows computed with LEAD, kind FULL -- same reasoning as dim_products and
  dim_salesperson. stg_product_prices is full dated history, not a snapshot, so
  SCD_TYPE_2_BY_TIME cannot be used: it would see several rows per (sku,
  price_tier) and have no way to decide which is current.

  This matters most right here. The tier prices are the first thing that will
  give Ref_Products real history, and a price introduced on 2025-06-01 must
  apply from 2025-06-01 -- not from whenever the pipeline first saw it, or
  every variance before that date is computed against the wrong standard.

  Grain is (sku, price_tier): the TT price can move without the SD price
  moving, and each gets its own window.
*/
MODEL (
  name marts.dim_product_price,
  kind FULL,
  cron '@daily',
  grain (product_price_key),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    unique_values(columns := (product_price_key)),
    not_null(columns := (product_price_key, sku, price_tier, unit_price, valid_from)),
    assert_no_overlapping_scd_windows(
      key           := sku,
      surrogate_key := product_price_key
    )
  )
);

WITH windowed AS (
  SELECT
    *,
    -- First version back-dated so a sale cannot predate its own price. See
    -- dim_products for the full rationale (SKU 60-178).
    CASE
      WHEN ROW_NUMBER() OVER (
             PARTITION BY sku, price_tier ORDER BY effective_from
           ) = 1
      THEN CAST('2024-10-01' AS TIMESTAMP)
      ELSE effective_from
    END AS valid_from,
    LEAD(effective_from) OVER (
      PARTITION BY sku, price_tier ORDER BY effective_from
    ) AS valid_to
  FROM staging.stg_product_prices
)

SELECT
  -- Hashed from the business key plus the effective date, for the same reason
  -- dim_products is: attribute-based hashes collide when a price reverts to a
  -- value it held before.
  MOD(
    @GENERATE_SURROGATE_KEY(
      TRIM(sku),
      price_tier,
      CAST(effective_from AS TEXT),
      hash_function := 'MD5_NUMBER_LOWER'
    ),
    9007199254740992
  ) AS product_price_key,

  sku,
  price_tier,
  unit_price,
  effective_from,
  valid_from,
  valid_to,
  valid_to IS NULL AS is_current

FROM windowed;