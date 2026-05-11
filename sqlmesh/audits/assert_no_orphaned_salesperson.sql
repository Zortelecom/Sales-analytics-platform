AUDIT (
  name assert_no_orphaned_salesperson,
  dialect duckdb
);
SELECT
  sales_line_id,
  sale_date,
  salesperson_id,
  salesperson_key
FROM @this_model
WHERE salesperson_id IS NOT NULL
  AND salesperson_key IS NULL;