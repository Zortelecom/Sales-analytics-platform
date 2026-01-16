MODEL (
  name marts.fact_targets,
  kind FULL,
  cron '@daily',
  grain (target_line_id),
  owner analytics_team,
  storage_format 'parquet',
  partitioned_by (target_year)
);

SELECT
  t.target_line_id,
  
  -- Date dimension FK
  CAST(STRFTIME(t.target_month, '%Y%m%d') AS INTEGER) AS date_key,
  t.target_month,
  EXTRACT(YEAR FROM t.target_month) AS target_year,
  EXTRACT(MONTH FROM t.target_month) AS target_month_num,
  
  -- Salesperson dimension FK (join to version valid at target month)
  t.salesperson_id,
  sp.salesperson_key,
  
  -- Product category
  t.product_category,
  
  -- MEASURES
  t.target_amount

FROM staging.stg_targets_data t

-- Join to salesperson dimension (SCD Type 2)
LEFT JOIN marts.dim_salesperson sp
  ON t.salesperson_id = sp.salesperson_id
  AND t.target_month >= sp.valid_from
  AND (t.target_month < sp.valid_to OR sp.valid_to IS NULL);