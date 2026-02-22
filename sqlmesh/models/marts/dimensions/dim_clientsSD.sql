MODEL (
  name marts.dim_clientsd,
  kind FULL,
  cron '@daily',
  grain (clientsd_key),
  owner analytics_team,
  storage_format 'parquet'
);


SELECT
  sd_key AS clientsd_key,
  sd_id,
  sd_name,
  region,
  subregion,
  city,
  key_player,
  phone_number,
  is_destocked,
  valid_from,
  valid_to
FROM staging.stg_clientsd_data
