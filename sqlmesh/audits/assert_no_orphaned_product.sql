AUDIT (
  name assert_no_orphaned_product,
  dialect duckdb
);
SELECT
  sales_line_id,
  sale_date,
  sku,
  product_key
FROM @this_model
WHERE sku IS NOT NULL
  AND product_key IS NULL;