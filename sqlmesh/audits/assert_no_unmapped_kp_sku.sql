AUDIT (
  name assert_no_unmapped_kp_sku
);

SELECT
  kp_sd_line_id,
  sale_date,
  source_sku,
  sku
FROM @this_model
WHERE source_sku IS NOT NULL
  AND NOT has_sku_mapping
  AND product_key IS NULL;
