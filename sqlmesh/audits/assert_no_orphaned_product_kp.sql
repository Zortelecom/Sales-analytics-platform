AUDIT (
  name assert_no_orphaned_product_kp,
  dialect duckdb
);
SELECT
  kp_sd_line_id,
  sale_date,
  sku,
  product_key
FROM @this_model
WHERE sku IS NOT NULL
  AND product_key IS NULL;
