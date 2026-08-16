/*
  Sell-in price conformance: charged price vs the SD-tier reference.

  Same question as assert_price_matches_tier, on fact_kp_sd, where the tier is
  always SD. Kept as a separate file because SQLMesh takes one AUDIT per file
  and the projected columns differ -- kp_sd_line_id and kp_name rather than
  sales_line_id and salesperson_id.

  Grouped by KP and SD, a cluster here is the interesting case: an SD being
  billed consistently off-list is a commercial conversation, and it is exactly
  the kind of quantifiable defect a sell-in/sell-out reconciliation exists to
  surface.
*/
AUDIT (
  name assert_price_matches_tier_kp,
  dialect duckdb,
  blocking false,
  defaults (
    tolerance_pct = 5.0
  )
);

SELECT
  kp_sd_line_id,
  sale_date,
  sku,
  kp_name,
  clientsd_id,
  destockage_channel,
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
        unit_price_standard IS NULL
     OR ABS(unit_price_effective - unit_price_standard)
        / NULLIF(unit_price_standard, 0) * 100 > @tolerance_pct
  );
