AUDIT (
  name assert_amount_matches_qty_x_price_kp,
  dialect duckdb
);
SELECT
  kp_sd_line_id,
  sale_date,
  sku,
  quantity,
  unit_price,
  total_amount,
  ROUND(quantity * unit_price, 2) AS expected_amount,
  ROUND(
    ABS(total_amount - (quantity * unit_price))
    / NULLIF(quantity * unit_price, 0) * 100,
    2
  ) AS deviation_pct
FROM @this_model
WHERE unit_price > 0
  AND quantity > 0
  AND ABS(total_amount - (quantity * unit_price))
      / NULLIF(quantity * unit_price, 0) > 0.01;
