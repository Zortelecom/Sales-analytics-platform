AUDIT (
  name assert_no_overlapping_scd_windows,
  description 'Ensures SCD Type-2 valid_from / valid_to windows do not overlap for the same business key.',
);

SELECT
  key,
  d1.valid_from AS window_1_from,
  d1.valid_to   AS window_1_to,
  d2.valid_from AS window_2_from,
  d2.valid_to   AS window_2_to
FROM @this_model d1
JOIN @this_model d2
  USING (key)
WHERE d1.valid_from < d2.valid_to
  AND d2.valid_from < d1.valid_to
  AND d1.row_id != d2.row_id