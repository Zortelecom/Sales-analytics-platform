MODEL (
  name staging.stg_clientsd_data,
  kind SCD_TYPE_2_BY_COLUMN (
    unique_key  (sd_id),
    columns (region, subregion, city, key_player, phone_number, is_destocked)
  ),
  cron '@daily',
  grain (sd_id),
  owner analytics_team,
  storage_format 'parquet'
);

SELECT
  md5_number_lower(CONCAT_WS('|',
    TRIM(sd_id),
    TRIM(region),
    TRIM(subregion),
    TRIM(city),
    TRIM(kp),
    TRIM(phone),
    LOWER(TRIM(is_destocked))
  )) AS sd_key,
  client_sd_ref_id,
  TRIM(sd_id) AS sd_id,
  TRIM(sd_name) AS sd_name,
  TRIM(region) AS region,
  TRIM(subregion) AS subregion,
  TRIM(city) AS city,
  TRIM(kp) AS key_player,
  TRIM(phone) AS phone_number,
  CASE 
    WHEN LOWER(TRIM(is_destocked)) IN ('true', 'yes', '1', 'oui') THEN TRUE
    ELSE FALSE
  END AS is_destocked

FROM raw.clientSD_data
WHERE TRIM(sd_id) IS NOT NULL 
  AND TRIM(sd_id) != '';