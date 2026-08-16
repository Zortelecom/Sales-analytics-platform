/*
  Which landing rows are current.

  Landing is append-only: a resubmitted workbook adds rows rather than
  replacing them. Supersession is resolved HERE, at read time, so every
  version of every file stays queryable for investigation while raw.* sees
  only the latest.

  UNIT OF SUPERSESSION IS THE FILE, not the row. An Excel workbook is a
  complete statement of its own contents, so "the newest ingest of this file
  wins" needs no per-source natural key and works identically for all eight
  tables.

  Its one limitation: a workbook that is renamed or split leaves its old rows
  current forever, because nothing supersedes them. Retire those explicitly
  with LandingWriter.retire_file(), which writes a status='retired' row that
  this model excludes.

  Every raw.* model joins this. Do not inline the logic -- one definition
  means supersession cannot drift between sources.
*/
MODEL (
  name raw.current_files,
  kind VIEW,
  owner analytics_team,
  description 'Latest ingested version of each landing source file, excluding retired files'
);

WITH latest AS (
  SELECT
    source_path,
    MAX(registered_at) AS registered_at
  FROM landing.file_registry
  WHERE status = 'ingested'
    AND source_path NOT IN (
      SELECT source_path FROM landing.file_registry WHERE status = 'retired'
    )
  GROUP BY source_path
)
SELECT
  r.source_path,
  r.file_sha256,
  r.source_type,
  r.source_file,
  r.landing_table,
  r.batch_id,
  r.row_count,
  r.registered_at
FROM landing.file_registry r
JOIN latest l
  ON r.source_path = l.source_path
 AND r.registered_at = l.registered_at
WHERE r.status = 'ingested';
