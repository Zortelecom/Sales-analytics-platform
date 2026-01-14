MODEL (
  name staging.stg_clientsd_data,
  kind FULL,
  cron '@daily',
  grain (sd_id),
  owner analytics_team,
  storage_format 'parquet'
);

SELECT
  client_sd_ref_id,
  TRIM(sd_id) AS sd_id,
  TRIM(sd_name) AS sd_name,
  TRIM(region) AS region,
  TRIM(subregion) AS subregion,
  TRIM(city) AS city,
  TRIM(kp) AS key_player_type,
  TRIM(phone) AS phone_number,
  CASE 
    WHEN LOWER(TRIM(is_destocked)) IN ('true', 'yes', '1', 'oui') THEN TRUE
    ELSE FALSE
  END AS is_destocked

FROM raw.clientSD_data
WHERE TRIM(sd_id) IS NOT NULL 
  AND TRIM(sd_id) != '';