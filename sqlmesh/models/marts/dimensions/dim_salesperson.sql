MODEL (
  name marts.dim_salesperson,
  kind FULL,
  cron '@daily',
  grain (salesperson_key),
  owner analytics_team,
  storage_format 'parquet'
);

SELECT
  salesperson_key,
  salesperson_id,
  salesperson_name,
  region,
  subregion,
  sales_channel,
  supervisor_name,
  valid_from,
  valid_to
FROM staging.stg_salesteam_data;