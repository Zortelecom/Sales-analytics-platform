/*
  Monthly targets per salesperson and product category.

  (2026-08) TRY_STRPTIME needs an explicit VARCHAR cast.

  month_year now arrives from landing as a TIMESTAMP, because the extractor
  preserves types instead of casting everything to str. TRY_STRPTIME only
  accepts VARCHAR, so the old call failed to bind:

      No function matches 'try_strptime(TIMESTAMP, STRING_LITERAL)'

  TRY_CAST comes first and handles the normal case; the strptime fallbacks
  stay for the case where a supervisor formats the cell as text and the
  extractor could not parse it as a date. Both paths verified.
*/
MODEL (
  name staging.stg_targets_data,
  kind FULL,
  cron '@daily',
  grain (target_line_id),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    unique_values(columns := (target_line_id)),
    not_null(columns := (
      target_line_id,
      target_month,
      salesperson_id,
      product_category,
      target_amount
    )),
    -- A zero or negative target is almost certainly a data entry error.
    accepted_range(column := target_amount, min_v := 0, inclusive := false)
  )
);

SELECT
  target_line_id,

  COALESCE(
    TRY_CAST(month_year AS DATE),
    TRY_STRPTIME(CAST(month_year AS VARCHAR), '%m/%Y')::DATE,
    TRY_STRPTIME(CAST(month_year AS VARCHAR), '%b-%Y')::DATE,
    TRY_STRPTIME(CAST(month_year AS VARCHAR), '%Y-%m')::DATE
  ) AS target_month,

  TRIM(salesperson_id)                     AS salesperson_id,
  TRIM(UPPER(product_category))            AS product_category,
  TRY_CAST(target_amount AS DECIMAL(12,2)) AS target_amount

FROM raw.targets_data
WHERE TRIM(salesperson_id) IS NOT NULL
  AND TRIM(salesperson_id) != ''
  AND TRIM(UPPER(product_category)) IS NOT NULL;