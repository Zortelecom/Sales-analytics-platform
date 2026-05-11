MODEL (
  name staging.stg_targets_data,
  kind FULL,
  cron '@daily',
  grain (target_line_id),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    -- Each target line must appear exactly once.
    unique_values(columns := (target_line_id)),

    -- All FK columns must be present for the row to be joinable
    -- from f_targets.
    not_null(columns := (
      target_line_id,
      target_month,
      salesperson_id,
      product_category,
      target_amount
    )),

    -- Targets must be positive; a zero or negative target is almost
    -- certainly a data entry error in Sales_Targets.xlsx.
    accepted_range(column := target_amount, min_v := 0, inclusive := false)
  )
);

SELECT
  target_line_id,
  
  -- Parse month_year (format: YYYY-MM-01)
  TRY_CAST(month_year AS DATE)            AS target_month,
  
  TRIM(salesperson_id)                    AS salesperson_id,
  TRIM(product_category)                  AS product_category,
  TRY_CAST(target_amount AS DECIMAL(12,2)) AS target_amount

FROM raw.targets_data
WHERE TRIM(salesperson_id) IS NOT NULL
  AND TRIM(product_category) IS NOT NULL;
