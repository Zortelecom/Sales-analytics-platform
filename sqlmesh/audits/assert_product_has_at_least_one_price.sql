/*
  Every product version must be priced in at least one tier.

  WHY THIS LIVES ON stg_products_data, NOT stg_product_prices
  ──────────────────────────────────────────────────────────
  stg_product_prices is long-form: a product with no price in any tier has
  ZERO rows there. Absence cannot be detected by querying the rows that exist.
  stg_products_data has exactly one row per (sku, effective_from), so the
  count of priced tiers is computable and an unpriced product is visible.

  A product priced in one tier and not another is CORRECT and must not fire --
  some products are not sold in traditional trade, others not in GMS. Zero
  tiers is the error: the product cannot be valued in any channel, so every
  line referencing it gets a NULL unit_price_standard and drops out of every
  variance measure without appearing anywhere as a problem.

  Blocking. Unlike a price that merely disagrees with the reference, this is a
  master-data gap with no correct downstream behaviour -- the product has no
  price at all.
*/
AUDIT (
  name assert_product_has_at_least_one_price,
  dialect duckdb
);

SELECT
  sku,
  product_name,
  product_category,
  effective_from,
  priced_tier_count
FROM @this_model
WHERE priced_tier_count = 0;
