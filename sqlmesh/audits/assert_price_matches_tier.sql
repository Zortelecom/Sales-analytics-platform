/*
  Conformance: is the price actually charged close to the reference price for
  that SKU, that channel, and that date?

  This is the check the old amount audit was accidentally performing, done
  correctly. The effective price is derived (total_amount / quantity), and the
  reference comes from dim_product_price at the tier the line belongs to --
  TT for traditional trade, GMS for grande et moyenne surface, SD for sell-in.

  blocking false, and deliberately so. A negotiated discount, a promotional
  price or a rounding convention will all show up here, and none of them is a
  reason to refuse to build the fact. What matters is the DISTRIBUTION: a
  handful of lines is commercial reality, a whole salesperson or a whole month
  is either a price list that was never updated or an SD being billed off-list.

  A NULL unit_price_standard means no price exists for that tier -- i.e. the
  product is being sold through a channel it has no price list for. That is a
  master-data gap, and it is included here rather than filtered out.
*/
AUDIT (
  name assert_price_matches_tier,
  dialect duckdb,
  blocking false,
  defaults (
    tolerance_pct = 5.0
  )
);

SELECT
  sales_line_id,
  sale_date,
  sku,
  sales_channel,
  price_tier,
  salesperson_id,
  clientsd_id,
  quantity,
  total_amount,
  unit_price_effective,
  unit_price_standard,
  ROUND(
    (unit_price_effective - unit_price_standard)
    / NULLIF(unit_price_standard, 0) * 100, 2
  ) AS price_variance_pct
FROM @this_model
WHERE quantity > 0
  AND total_amount > 0
  AND (
        unit_price_standard IS NULL           -- no price list for this tier
     OR ABS(unit_price_effective - unit_price_standard)
        / NULLIF(unit_price_standard, 0) * 100 > @tolerance_pct
  );
