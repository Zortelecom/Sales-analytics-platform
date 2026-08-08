MODEL (
    name staging.stg_clientsd_data,
    kind FULL,
    cron '@daily',
    grain (sd_id, effective_from),
    owner analytics_team,
    storage_format 'parquet',
    audits (
        unique_combination_of_columns (
            columns := (sd_id, effective_from)
        ),
        not_null (
            columns := (
                sd_id,
                sd_name,
                region,
                subregion,
                supervisor_name,
                city,
                key_player,
                is_destocked,
                effective_from
            )
        )
    )
);

SELECT
    client_sd_ref_id,
    TRIM(sd_id) AS sd_id,
    TRIM(sd_name) AS sd_name,
    TRIM(region) AS region,
    TRIM(subregion) AS subregion,
    TRIM(supervisor) AS supervisor_name,
    TRIM(city) AS city,
    TRIM(kp) AS key_player,
    TRIM(phone) AS phone_number,
    CASE
        WHEN LOWER(TRIM(is_destocked)) IN ('true', 'yes', '1', 'oui') THEN TRUE
        ELSE FALSE
    END AS is_destocked,
    TRY_CAST (effective_from AS TIMESTAMP) AS effective_from
FROM raw.clientsd_data
WHERE
    TRIM(sd_id) IS NOT NULL
    AND TRIM(sd_id) != ''
    AND TRY_CAST (effective_from AS TIMESTAMP) IS NOT NULL;