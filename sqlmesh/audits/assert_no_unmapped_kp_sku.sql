/*
  A KP SKU that neither arrived pre-resolved nor resolves in dim_products.

  Supervisors resolve KP-native SKUs inside the workbook before pasting, so
  sku_was_remapped is FALSE on essentially every row -- the guard that makes
  this audit meaningful is `product_key IS NULL`.

  Strictly a subset of assert_no_orphaned_product_kp, which fires on every
  NULL product_key. Kept because its output columns (source_sku alongside sku)
  make the supervisor-side cause immediately visible; delete it if you would
  rather not run two audits over the same rows.

  (2026-08) has_sku_mapping renamed to sku_was_remapped; dialect declared to
  match the other audit files.
*/
AUDIT (
  name assert_no_unmapped_kp_sku,
  dialect duckdb
);

SELECT
  kp_sd_line_id,
  sale_date,
  source_sku,
  sku
FROM @this_model
WHERE source_sku IS NOT NULL
  AND NOT sku_was_remapped
  AND product_key IS NULL;
