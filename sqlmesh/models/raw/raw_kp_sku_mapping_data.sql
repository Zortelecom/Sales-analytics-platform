/*
  KP-native SKU to internal SKU mapping.

  Was: kind SEED over ../../seeds/kp_sku_mapping_data.csv
  Now: a view over the append-only landing table, filtered to the current
  version of each source file.

  The MODEL NAME IS UNCHANGED, so every staging model that reads
  raw.kp_sku_mapping_data needs no edit.

  What changed for consumers: columns arrive TYPED. The old SEED path ran
  through CSV after BaseExcelExtractor had already cast everything to str,
  so a date was the string '2025-12-01 00:00:00' and a missing cell was the
  string 'nan'. Casts in staging still work (CAST(DATE AS DATE) is a no-op),
  but any staging logic relying on string behaviour of a now-typed column
  needs checking -- and `IS NOT NULL` guards now actually catch missing
  values, which they did not before.

  Provenance columns are prefixed with _ and pass straight through:
  _source_file, _sheet_name, _table_name trace a row to a supervisor's tab.
*/
MODEL (
  name raw.kp_sku_mapping_data,
  kind VIEW,
  owner analytics_team,
  description 'KP-native SKU to internal SKU mapping'
);

SELECT t.*
FROM landing.kp_sku_mapping_data t
JOIN raw.current_files c
  ON t._source_path = c.source_path
 AND t._file_sha256 = c.file_sha256;
