MODEL (
  name marts.dim_date,
  kind FULL,
  cron '@monthly',
  grain (date_key),
  owner analytics_team,
  storage_format 'parquet',
  description 'Date dimension with calendar attributes'
);

WITH date_spine AS (
  SELECT UNNEST(
    generate_series(
      DATE '2025-01-01',
      DATE '2035-12-31',
      INTERVAL '1 day'
    )
  ) AS date_value
)

SELECT
  -- Surrogate key (YYYYMMDD as integer)
  CAST(STRFTIME(date_value, '%Y%m%d') AS INTEGER) AS date_key,
  
  -- Natural key
  date_value AS date,
  
  -- Calendar hierarchy
  EXTRACT('year' FROM date_value) AS year,
  EXTRACT('quarter' FROM date_value) AS quarter,
  EXTRACT('month' FROM date_value) AS month,
  EXTRACT('week' FROM date_value) AS week_of_year,
  EXTRACT('day' FROM date_value) AS day_of_month,
  EXTRACT('dow' FROM date_value) AS day_of_week,
  
  -- Formatted strings for display
  STRFTIME(date_value, '%Y') AS year_name,
  CONCAT(STRFTIME(date_value, '%Y'), '-Q', EXTRACT('quarter' FROM date_value)) AS quarter_name,
  STRFTIME(date_value, '%Y-%m') AS year_month,
  STRFTIME(date_value, '%B %Y') AS month_name,
  STRFTIME(date_value, '%A') AS day_name,
  
  -- Period start dates
  DATE_TRUNC('month', date_value) AS month_start_date,
  DATE_TRUNC('quarter', date_value) AS quarter_start_date,
  DATE_TRUNC('year', date_value) AS year_start_date,
  
  -- Flags
  CASE WHEN EXTRACT('DOW' FROM date_value) IN (0, 6) THEN TRUE ELSE FALSE END AS is_weekend,
  CASE WHEN date_value = DATE_TRUNC('month', date_value) THEN TRUE ELSE FALSE END AS is_month_start,
  CASE WHEN date_value = LAST_DAY(date_value) THEN TRUE ELSE FALSE END AS is_month_end

FROM date_spine;