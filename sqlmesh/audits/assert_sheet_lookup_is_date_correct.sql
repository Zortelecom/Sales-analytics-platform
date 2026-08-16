/*
  Does the workbook's own VLOOKUP reflect the price in force on the sale date?

  This audit exists to detect a spreadsheet defect the warehouse is otherwise
  immune to. When Ref_Products gains a second dated row for a SKU, a plain
  VLOOKUP returns whichever row it matches first -- so every historical line in
  the sheet silently re-prices to the newest value. The supervisor's own pivot
  tables then disagree with the warehouse, and the supervisor is not wrong to
  trust their pivot.

  fact_sales resolves unit_price_standard through dim_product_price's SCD
  window, so the warehouse figure is right regardless. What this catches is the
  drift between the two, which is what turns into "the report doesn't match my
  file".

  A cluster of rows all from before a known price change is the signature of a
  date-blind lookup. See docs/SCD_AND_LOOKUPS.md for the formula that fixes it.

  blocking false: the sheet's price is not used in any measure. This is a
  signal about the workbook, not about the data.
*/
AUDIT (
  name assert_sheet_lookup_is_date_correct,
  dialect duckdb,
  blocking false,
  defaults (
    tolerance_pct = 0.5
  )
);

SELECT
  sales_line_id,
  sale_date,
  sku,
  price_tier,
  unit_price_sheet,
  unit_price_standard,
  ROUND(
    (unit_price_sheet - unit_price_standard)
    / NULLIF(unit_price_standard, 0) * 100, 2
  ) AS sheet_drift_pct
FROM @this_model
WHERE price_tier = 'TT'          -- the sheet only ever looks up the TT price
  AND unit_price_sheet > 0
  AND unit_price_standard > 0
  AND ABS(unit_price_sheet - unit_price_standard)
      / NULLIF(unit_price_standard, 0) * 100 > @tolerance_pct;
