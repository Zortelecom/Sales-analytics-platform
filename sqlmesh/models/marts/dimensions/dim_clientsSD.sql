MODEL (
  name marts.dim_clientsd,
  kind SCD_TYPE_2_BY_COLUMN (
    unique_key (sd_id),
    columns [
      sd_name,
      region,
      subregion,
      key_player,
      is_destocked
    ],
    updated_at_name effective_from,
    batch_size 1
  ),
  start '2024-10-01',
  cron '@monthly',
  grain (clientsd_key),
  owner analytics_team,
  storage_format 'parquet',
  audits (
    unique_values(columns := (clientsd_key)),
    not_null(columns := (clientsd_key, sd_id)),
    assert_no_overlapping_scd_windows(
        key            := sd_id,
        surrogate_key  := clientsd_key
    )
  )
);

SELECT
  @GENERATE_SURROGATE_KEY(
    TRIM(sd_id),
    sd_name,
    region,
    subregion,
    key_player,
    is_destocked,
    CAST(effective_from AS TEXT),
    hash_function := 'MD5_NUMBER_LOWER'
  ) AS clientsd_key,

  sd_id,
  sd_name,
  region,
  subregion,
  city,
  key_player,
  phone_number,
  is_destocked,
  effective_from

FROM staging.stg_clientsd_data

WHERE CAST(effective_from AS DATE) BETWEEN @start_ds AND @end_ds;