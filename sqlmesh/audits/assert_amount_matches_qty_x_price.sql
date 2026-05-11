AUDIT (
  name assert_amount_matches_qty_x_price,
  dialect duckdb
);
SELECT
  sales_line_id,
  sale_date,
  sku,
  quantity,
  unit_price_actual,
  total_amount,
  ROUND(quantity * unit_price_actual, 2) AS expected_amount,
  ROUND(
    ABS(total_amount - (quantity * unit_price_actual))
    / NULLIF(quantity * unit_price_actual, 0) * 100,
    2
  ) AS deviation_pct
FROM @this_model
WHERE unit_price_actual > 0
  AND quantity > 0
  AND ABS(total_amount - (quantity * unit_price_actual))
      / NULLIF(quantity * unit_price_actual, 0) > 0.01;