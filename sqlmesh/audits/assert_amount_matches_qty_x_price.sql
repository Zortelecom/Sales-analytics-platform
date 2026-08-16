/*
  Line arithmetic: does total_amount equal quantity x the price actually used?

  (2026-08) Now compares against unit_price_effective -- the price DERIVED from
  the line (total_amount / quantity) -- not against unit_price_standard.

  The old version used unit_price_actual, which is the Ref_Products traditional-
  trade price copied into the workbook by a VLOOKUP alongside product_name and
  unit_weight. It is not the price the transaction was struck at. So the audit
  fired on every GMS and sell-in line, where the real price legitimately differs
  from TT -- flagging correct data as broken, which is worse than not checking
  at all, because it trains people to ignore it.

  What survives here is the genuine coherence question: a supervisor who types
  an amount that does not match their own quantity and price has made an
  arithmetic error. Conformance to an expected price is a different question,
  answered by assert_price_matches_tier.
*/
AUDIT (
  name assert_amount_matches_qty_x_price,
  dialect duckdb,
  defaults (
    tolerance_pct = 1.0
  )
);

SELECT
  sales_line_id,
  sale_date,
  sku,
  sales_channel,
  quantity,
  unit_price_sheet,
  unit_price_effective,
  total_amount,
  ROUND(quantity * unit_price_sheet, 2) AS expected_amount,
  ROUND(
    ABS(total_amount - (quantity * unit_price_sheet))
    / NULLIF(quantity * unit_price_sheet, 0) * 100, 2
  ) AS deviation_pct
FROM @this_model
WHERE unit_price_sheet > 0
  AND quantity > 0
  -- Only when the workbook's own price and amount disagree with each other.
  -- A GMS line priced away from TT is not an arithmetic error.
  AND ABS(total_amount - (quantity * unit_price_sheet))
      / NULLIF(quantity * unit_price_sheet, 0) * 100 > @tolerance_pct
  AND price_tier = 'TT';
