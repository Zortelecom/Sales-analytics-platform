AUDIT (
  name assert_amount_is_integer_xaf_kp,
  dialect duckdb
);
SELECT
  kp_sd_line_id,
  sale_date,
  sku,
  total_amount
FROM @this_model
WHERE total_amount != FLOOR(total_amount);
