MODEL (
  name marts.dim_clientsd,
  kind FULL,
  cron '@daily',
  grain (clientsd_key),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    -- Built-in: primary key integrity.
    unique_values(columns := (clientsd_key)),
    not_null(columns := (clientsd_key, sd_id)),

    -- Custom: SCD window overlap check — see audits/*.
     assert_no_overlapping_scd_windows(
        key            := sd_id,
        surrogate_key  := clientsd_key
    )
  )
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
