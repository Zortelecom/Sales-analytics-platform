/*
  Every column currently in landing, with its type.

  Schema drift detector. Landing evolves permissively -- a new column in a
  workbook is added rather than rejected -- which is right for bronze but means
  drift arrives silently. Diff this between runs, or read it after a supervisor
  changes a template.

  A column showing VARCHAR where you expect a number means the extractor found
  a value it could not parse and fell back to text. That is the first place to
  look when a staging CAST starts failing.

  (2026-08) duckdb_columns() rather than information_schema.columns.
  information_schema is a TABLE reference, so SQLMesh registered it as an
  external model and tried to DESCRIBE it inside the DuckLake catalog, where
  no such schema exists:

      Unable to get schema for '"sales_lakehouse"."information_schema"."columns"'

  duckdb_columns() is a table FUNCTION, so the dependency parser leaves it
  alone. It also reports the catalog, which information_schema does not make
  as easy to filter on.
*/
MODEL (
  name meta.column_inventory,
  kind VIEW,
  owner analytics_team,
  description 'Landing schema as it currently stands, for drift detection'
);

SELECT
  table_name   AS landing_table,
  column_name,
  data_type,
  column_index AS ordinal_position,
  starts_with(column_name, '_') AS is_provenance
FROM duckdb_columns()
WHERE schema_name = 'landing'
  AND table_name <> 'file_registry';