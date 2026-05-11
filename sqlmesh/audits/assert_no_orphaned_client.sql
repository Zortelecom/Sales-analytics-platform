AUDIT (
  name assert_no_orphaned_client,
  dialect duckdb
);
SELECT
  sales_line_id,
  sale_date,
  clientsd_id,
  clientsd_key
FROM @this_model
WHERE clientsd_id IS NOT NULL
  AND clientsd_key IS NULL;