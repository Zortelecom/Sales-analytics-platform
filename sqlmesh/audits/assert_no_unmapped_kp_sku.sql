AUDIT (
  name assert_no_unmapped_kp_sku
);

SELECT
  s.*
FROM @this_model s
LEFT JOIN marts.dim_products p ON s.sku = p.sku
WHERE p.sku IS NULL;