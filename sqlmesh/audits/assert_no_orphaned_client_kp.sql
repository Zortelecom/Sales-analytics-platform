AUDIT (
  name assert_no_orphaned_client_kp,
  dialect duckdb
);
SELECT
  kp_sd_line_id,
  sale_date,
  clientsd_id,
  clientsd_key
FROM @this_model
WHERE clientsd_id IS NOT NULL
  AND clientsd_key IS NULL;
