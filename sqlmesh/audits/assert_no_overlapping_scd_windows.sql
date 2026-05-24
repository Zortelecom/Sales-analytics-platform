AUDIT (
  name assert_no_overlapping_scd_windows,
  dialect duckdb
  -- description 'Ensures SCD Type-2 valid_from / valid_to windows do not overlap for the same business key.',
);

SELECT
  d1.@key             AS business_key,
  d1.valid_from       AS window_1_from,
  d1.valid_to         AS window_1_to,
  d2.valid_from       AS window_2_from,
  d2.valid_to         AS window_2_to
FROM @this_model d1
JOIN @this_model d2
  ON d1.@key = d2.@key          -- natural/business key: groups rows for the same entity
WHERE d1.valid_from  < d2.valid_to
  AND d2.valid_from  < d1.valid_to
  AND d1.@surrogate_key != d2.@surrogate_key  -- exclude self-join