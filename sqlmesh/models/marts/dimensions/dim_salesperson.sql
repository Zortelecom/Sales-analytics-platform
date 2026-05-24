MODEL (
  name marts.dim_salesperson,
  kind FULL,
  cron '@daily',
  grain (salesperson_key),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    -- Built-in: primary key integrity.
    unique_values(columns := (salesperson_key)),
    not_null(columns := (salesperson_key, salesperson_id)),

    -- Custom: SCD window overlap check — see audits/*.
     assert_no_overlapping_scd_windows(
        key            := salesperson_id,
        surrogate_key  := salesperson_key
    )
  )
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