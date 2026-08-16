/*
  Freshness, anchored to the evaluation interval instead of wall clock.

  The previous version compared MAX(sale_date) against CURRENT_DATE - 7 days,
  which failed every backfill and every historical restatement: rebuilding
  October 2024 would always look 600+ days stale.

  IMPORTANT -- attach this to an INCREMENTAL_BY_TIME_RANGE model (fact_sales),
  not to stg_sales_data. staging models are `kind FULL`, where @end_ds resolves
  to the current run and you are back to comparing against today.

  blocking false: a freshness miss is information. A stale-but-correct mart is
  more useful than no mart, and a hard failure here blocks every downstream
  model over a reporting-cadence question.

  This is still a flat-window heuristic and will false-positive on holidays,
  a supervisor on leave, and KPs that invoice twice a month. It is a stopgap
  until meta.freshness tracks per-source expected cadence.
*/
AUDIT (
  name assert_sales_data_is_fresh,
  dialect duckdb,
  blocking false,
  defaults (
    max_lag_days = 7
  )
);

WITH observed AS (
  SELECT MAX(sale_date) AS max_sale_date
  FROM @this_model
)
SELECT
  max_sale_date,
  CAST(@end_ds AS DATE)                                    AS interval_end,
  DATE_DIFF('day', max_sale_date, CAST(@end_ds AS DATE))   AS lag_days,
  @max_lag_days                                            AS tolerance_days
FROM observed
WHERE max_sale_date IS NULL
   OR DATE_DIFF('day', max_sale_date, CAST(@end_ds AS DATE)) > @max_lag_days;
