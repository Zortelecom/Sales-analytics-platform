MODEL (
  name staging.stg_salesteam_data,
  kind FULL,
  cron '@daily',
  grain (salesteam_ref_id),
  owner analytics_team,
  storage_format 'parquet'
);

SELECT
  salesteam_ref_id,
  TRIM(salesperson_id) AS salesperson_id,
  TRIM(fullname) AS salesperson_name,
  TRIM(region) AS region,
  TRIM(subregion) AS subregion,
  TRIM(channel) AS sales_channel,
  TRIM(supervisor) AS supervisor_name

FROM raw.salesteam_data
WHERE TRIM(salesperson_id) IS NOT NULL;