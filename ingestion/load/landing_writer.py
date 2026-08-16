"""
Append-only landing writer. Replaces SeedWriter.

Writes extractor output straight into the `landing` schema of the DuckLake
catalog SQLMesh reads, with full provenance. There is no CSV in the path.

WHY APPEND-ONLY
───────────────
Seeds are overwritten on every run, so a corrected workbook silently replaces
its predecessor and the before/after is unrecoverable. (Concretely: the
destockage misfiling found by assert_destockage_channel_matches_sd was fixed
by editing the source workbooks, and there is now no way to quantify what was
wrong.) Landing appends, and supersession is resolved at read time --
`current_rows()` below -- so every version of every file stays queryable.

SUPERSESSION MODEL
──────────────────
An Excel file is a complete statement of its own contents, so the unit of
supersession is the FILE, not the row: for each source_path, only rows from
the most recent ingest are current. This needs no per-source natural key,
which is what makes it work uniformly across all four sources.

Its limitation: a file that is renamed or split leaves its old rows current
forever. Retire those explicitly via `retire_file()`.

WHAT REPLACED SeedWriter
────────────────────────
SeedWriter dropped source_file, sheet_name, table_name, ingestion_ts and
ingestion_batch_id before writing the CSV -- the extractor was already
producing per-row provenance and it was being discarded at the last step.
Those five columns are kept here, renamed to _-prefixed provenance so they
cannot be confused with business columns. A landing row therefore traces back
to a specific table, on a specific sheet, in a specific workbook.

CONNECTION SAFETY
─────────────────
The DuckLake catalog is a DuckDB file and takes one writer at a time. Use this
as a context manager so the connection closes before SQLMesh opens the lake.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)

# Columns BaseExcelExtractor already adds, renamed at the landing boundary so
# every provenance column carries the _ prefix. SeedWriter used to drop these.
EXTRACTOR_PROVENANCE_RENAME = {
    "source_file": "_source_file",
    "sheet_name": "_sheet_name",
    "table_name": "_table_name",
    "ingestion_ts": "_extracted_at",
    "ingestion_batch_id": "_batch_id",
}

# Prefixed with _ so they cannot collide with a supervisor's column name.
PROVENANCE_COLUMNS: List[str] = [
    "_batch_id",
    "_source_type",
    "_source_path",
    "_source_file",
    "_sheet_name",
    "_table_name",
    "_file_sha256",
    "_extractor",
    "_extracted_at",
    "_ingested_at",
    "_row_num",
]

# DDL for every landing table lives in ingestion/config/landing.py, so the
# schema owner owns its shape. That includes audit_results/audit_failures,
# which orchestration writes but which must EXIST before `sqlmesh plan` can
# build the meta views over them -- see the note there.
from ingestion.config.landing import LANDING_DDL, OBSERVABILITY_TABLES



def sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    """Content hash of a file. Used for duplicate detection and provenance."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


_NUMERIC_PREFIXES = (
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
    "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT",
    "FLOAT", "DOUBLE", "REAL", "DECIMAL", "NUMERIC",
)


def _stabilize_all_null_text(df: pd.DataFrame) -> pd.DataFrame:
    """
    Force object columns that are entirely null IN THIS SLICE to text dtype.

    The extractor types each column consistently across the whole directory,
    but append_directory_frame writes one FILE at a time -- and DuckDB infers
    from the slice, not from the parent frame. A column that is text overall
    but blank in the workbook that happens to sort first (sd_name in the
    Centre KP file) is inferred as INTEGER, and the next file's real values
    then force a table rebuild.

    Only object columns are at risk: numeric columns already carry a pandas
    nullable dtype and temporal ones carry datetime64, both of which DuckDB
    reads correctly even when every value is null.
    """
    for column in df.columns:
        if df[column].dtype == object and df[column].isna().all():
            df[column] = pd.array([None] * len(df), dtype="string")
    return df


def _is_numeric(duckdb_type: str) -> bool:
    """DECIMAL carries precision (DECIMAL(18,2)), so match on the prefix."""
    return duckdb_type.upper().startswith(_NUMERIC_PREFIXES)


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class LandingWriter:
    """
    Usage:

        with LandingWriter(catalog, data_path, batch_id) as writer:
            writer.append("sales", df, source_type="sales",
                          source_path=path, extractor="SalesExtractor")
    """

    def __init__(
        self,
        catalog_path: Path,
        data_path: Path,
        batch_id: str,
        schema: str = "landing",
        registry: str = "file_registry",
        catalog_alias: str = "sales_lakehouse",
        evolve_schema: bool = True,
    ) -> None:
        self.catalog_path = Path(catalog_path)
        self.data_path = Path(data_path)
        self.batch_id = batch_id
        self.schema = schema
        self.registry = registry
        self.evolve_schema = evolve_schema
        self._con: Optional[duckdb.DuckDBPyConnection] = None
        self._catalog_name: Optional[str] = None
        self.catalog_alias = catalog_alias
        # A plain .db/.duckdb path skips the DuckLake attach entirely -- what
        # the unit tests use, and a working fallback if the ducklake extension
        # is unavailable on a machine.
        self._use_ducklake = self.catalog_path.suffix == ".ducklake"

    # ── lifecycle ──────────────────────────────────────────────────────────

    def __enter__(self) -> "LandingWriter":
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def connect(self) -> None:
        # Whether the catalog exists must be read BEFORE any mkdir.
        catalog_existed = self.catalog_path.exists()
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True)
        self.data_path.mkdir(parents=True, exist_ok=True)

        if self._use_ducklake:
            self._con = duckdb.connect()
            self._con.execute("INSTALL ducklake; LOAD ducklake;")
            # DATA_PATH is stored in the catalog at creation. Passing it again
            # when attaching an EXISTING catalog is at best redundant and at
            # worst a conflict -- and this attaches the catalog SQLMesh already
            # created, so it is always the existing case in practice.
            attach = (
                f"ATTACH IF NOT EXISTS 'ducklake:{self.catalog_path.as_posix()}' "
                f"AS {self.catalog_alias}"
            )
            if not catalog_existed:
                attach += f" (DATA_PATH '{self.data_path.as_posix()}/')"
            self._con.execute(attach)
            self._con.execute(f"USE {self.catalog_alias}")
        else:
            self._con = duckdb.connect(str(self.catalog_path))

        # Always fully qualify. A catalog whose name matches the schema name
        # (e.g. a file called landing.db) makes a bare `landing.tbl` ambiguous.
        self._catalog_name = self._con.execute("SELECT current_catalog()").fetchone()[0]

        self._con.execute(f"CREATE SCHEMA IF NOT EXISTS {self.qschema}")
        for ddl in LANDING_DDL:
            self._con.execute(ddl.format(schema=self.qschema))

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    @property
    def qschema(self) -> str:
        """Catalog-qualified schema reference, e.g. \"lake\".\"landing\"."""
        if self._catalog_name is None:
            return f'"{self.schema}"'
        return f'"{self._catalog_name}"."{self.schema}"'

    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            raise RuntimeError("LandingWriter is not connected; use it as a context manager")
        return self._con

    # ── registry ───────────────────────────────────────────────────────────

    def already_ingested(self, file_sha256: str, table: Optional[str] = None) -> bool:
        """
        Has this exact file already been ingested INTO THIS TABLE?

        The table matters. References.xlsx produces four landing tables, so a
        hash-only check let the first one register the file and silently block
        the other three -- products_data, clientsd_data and kp_sku_mapping_data
        never landed, with nothing in the log but "unchanged since last ingest".
        """
        sql = (
            f"SELECT COUNT(*) FROM {self.qschema}.{self.registry} "
            f"WHERE file_sha256 = ? AND status = 'ingested'"
        )
        params = [file_sha256]
        if table is not None:
            sql += " AND landing_table = ?"
            params.append(table)
        row = self.con.execute(sql, params).fetchone()
        return bool(row and row[0])

    def register(
        self,
        *,
        source_path: Path,
        source_type: str,
        file_sha256: str,
        status: str,
        landing_table: Optional[str] = None,
        extractor: Optional[str] = None,
        row_count: int = 0,
        error: Optional[str] = None,
    ) -> None:
        stat = source_path.stat() if source_path.exists() else None
        self.con.execute(
            f"INSERT INTO {self.qschema}.{self.registry} VALUES "
            f"(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                file_sha256,
                source_type,
                str(source_path),
                source_path.name,
                stat.st_size if stat else None,
                datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).replace(tzinfo=None)
                if stat
                else None,
                self.batch_id,
                extractor,
                landing_table,
                row_count,
                status,
                error,
                datetime.now(timezone.utc).replace(tzinfo=None),
            ],
        )

    def retire_file(self, source_path: str, reason: str = "retired") -> None:
        """
        Exclude a file's rows from current_rows() without deleting history.
        Use when a workbook is renamed or split, since file-level supersession
        cannot detect that on its own.
        """
        self.register(
            source_path=Path(source_path),
            source_type="",
            file_sha256="",
            status="retired",
            error=reason,
        )

    # ── writing ────────────────────────────────────────────────────────────

    def _table_columns(self, table: str) -> List[str]:
        rows = self.con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_catalog = ? AND table_schema = ? AND table_name = ? "
            "ORDER BY ordinal_position",
            [self._catalog_name, self.schema, table],
        ).fetchall()
        return [r[0] for r in rows]

    def _table_exists(self, table: str) -> bool:
        return bool(self._table_columns(table))

    def append(
        self,
        table: str,
        df: pd.DataFrame,
        *,
        source_type: str,
        source_path: Path,
        file_sha256: Optional[str] = None,
        extractor: Optional[str] = None,
    ) -> int:
        """
        Append a frame to landing.<table>, stamping provenance. Returns rows written.

        The frame is not mutated: provenance is added to a shallow copy.
        """
        if df is None or df.empty:
            return 0

        file_sha256 = file_sha256 or sha256_of(source_path)
        stamped = df.copy().rename(columns=EXTRACTOR_PROVENANCE_RENAME)
        stamped["_batch_id"] = self.batch_id
        stamped["_source_type"] = source_type
        stamped["_source_path"] = str(source_path)
        # _source_file / _sheet_name / _table_name come from the extractor when
        # present; only fill in what is missing.
        if "_source_file" not in stamped.columns:
            stamped["_source_file"] = source_path.name
        stamped["_file_sha256"] = file_sha256
        stamped["_extractor"] = extractor or ""
        stamped["_ingested_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
        # Position within this file's extraction, for tracing a row back to a
        # spreadsheet row. Meaningful only because extractors preserve order.
        stamped["_row_num"] = range(len(stamped))
        stamped = _stabilize_all_null_text(stamped)

        self.con.register("_incoming", stamped)
        try:
            if not self._table_exists(table):
                self.con.execute(
                    f"CREATE TABLE {self.qschema}.{table} AS SELECT * FROM _incoming"
                )
            else:
                self._reconcile_columns(table, stamped)
                projection = self._align_incoming_to_table(table)
                # BY NAME tolerates a frame missing columns the table has
                # (they land NULL) -- a dropped source column is not fatal.
                self.con.execute(
                    f"INSERT INTO {self.qschema}.{table} BY NAME "
                    f"SELECT {projection} FROM _incoming"
                )
        finally:
            self.con.unregister("_incoming")

        self.register(
            source_path=source_path,
            source_type=source_type,
            file_sha256=file_sha256,
            status="ingested",
            landing_table=table,
            extractor=extractor,
            row_count=len(stamped),
        )
        return len(stamped)

    def append_directory_frame(
        self,
        table: str,
        df: pd.DataFrame,
        *,
        source_type: str,
        input_dir: Path,
        extractor: Optional[str] = None,
        skip_duplicates: bool = True,
    ) -> Dict[str, int]:
        """
        Write a frame produced by extract_from_directory(), splitting it back
        into per-file appends using the extractor's own source_file column.

        This is why the extractor's per-row provenance matters: a directory
        extraction returns one undifferentiated frame, and file-level
        supersession needs to know which workbook each row came from.
        """
        if df is None or df.empty:
            return {}
        if "source_file" not in df.columns:
            raise ValueError(
                f"{table}: frame has no source_file column, so rows cannot be "
                f"attributed to a workbook. BaseExcelExtractor adds it; a "
                f"subclass has probably dropped it."
            )

        written: Dict[str, int] = {}
        for file_name, group in df.groupby("source_file", sort=True):
            source_path = Path(input_dir) / str(file_name)
            if not source_path.exists():
                logger.warning("%s: %s not found, skipping", table, source_path)
                continue

            digest = sha256_of(source_path)
            if skip_duplicates and self.already_ingested(digest, table):
                self.register(
                    source_path=source_path, source_type=source_type,
                    file_sha256=digest, status="skipped_duplicate",
                    landing_table=table,
                )
                logger.info("%s: %s unchanged since last ingest, skipped", table, file_name)
                continue

            written[str(file_name)] = self.append(
                table,
                group.reset_index(drop=True),
                source_type=source_type,
                source_path=source_path,
                file_sha256=digest,
                extractor=extractor,
            )
        return written

    def _column_types(self, table: str) -> Dict[str, str]:
        rows = self.con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_catalog = ? AND table_schema = ? AND table_name = ? "
            "ORDER BY ordinal_position",
            [self._catalog_name, self.schema, table],
        ).fetchall()
        return {name: dtype.upper() for name, dtype in rows}

    def _align_incoming_to_table(self, table: str) -> str:
        """
        Projection that makes _incoming insertable into an existing landing
        table, and returns the column list to SELECT.

        THE QUESTION IS NEVER "do the declared types differ" -- it is "does the
        data survive the cast". An earlier version compared types and tried to
        ALTER on any mismatch, which broke a case DuckDB already handled: the
        table held unit_weight as DOUBLE, the batch brought VARCHAR, and every
        value parsed cleanly. DuckLake then rejected the ALTER, because it
        permits widening promotions only and DOUBLE -> VARCHAR is not one.

        So: TRY_CAST every conflicting column to the table's type and count the
        values that would be lost. None lost -> cast and insert. Some lost ->
        the table genuinely has to widen, handled below.
        """
        table_types = self._column_types(table)
        # DESCRIBE returns six columns; index rather than unpack.
        incoming = {
            row[0]: str(row[1]).upper()
            for row in self.con.execute("DESCRIBE SELECT * FROM _incoming").fetchall()
        }

        projection: List[str] = []
        widen: Dict[str, str] = {}

        for column, incoming_type in incoming.items():
            target = table_types.get(column)
            if target is None or target == incoming_type:
                projection.append(f'"{column}"')
                continue

            lost = self.con.execute(
                f'SELECT COUNT(*) FROM _incoming '
                f'WHERE "{column}" IS NOT NULL '
                f'  AND TRY_CAST("{column}" AS {target}) IS NULL'
            ).fetchone()[0]

            if lost == 0:
                logger.info(
                    "landing.%s: casting %r from %s to the table's %s "
                    "(no values lost)", table, column, incoming_type, target,
                )
                projection.append(f'CAST("{column}" AS {target}) AS "{column}"')
            else:
                logger.warning(
                    "landing.%s: %d value(s) in %r cannot be cast from %s to %s "
                    "— widening the column to VARCHAR",
                    table, lost, column, incoming_type, target,
                )
                widen[column] = "VARCHAR"
                projection.append(f'CAST("{column}" AS VARCHAR) AS "{column}"')

        if widen:
            self._widen_table_columns(table, widen)

        return ", ".join(projection)

    def _widen_table_columns(self, table: str, widen: Dict[str, str]) -> None:
        """
        Widen existing landing columns, rebuilding the table if ALTER cannot.

        DuckLake allows widening promotions only, so DOUBLE -> VARCHAR is
        refused even though it loses nothing. Rebuilding is the fallback:
        landing is bronze, VARCHAR keeps every value readable, and staging
        casts anyway. The alternative -- rejecting the batch -- loses real data
        over a formatting inconsistency in one cell.
        """
        remaining = {}
        for column, target in widen.items():
            try:
                self.con.execute(
                    f'ALTER TABLE {self.qschema}.{table} '
                    f'ALTER COLUMN "{column}" TYPE {target}'
                )
                logger.info("landing.%s: %r widened to %s in place",
                            table, column, target)
            except Exception as exc:  # noqa: BLE001 -- expected on DuckLake
                logger.info("landing.%s: in-place widen of %r refused (%s), "
                            "rebuilding the table", table, column, exc)
                remaining[column] = target

        if not remaining:
            return

        ordered = self._table_columns(table)
        projection = ", ".join(
            f'CAST("{c}" AS {remaining[c]}) AS "{c}"' if c in remaining else f'"{c}"'
            for c in ordered
        )
        staging = f"{table}__widening"
        try:
            self.con.execute(f"DROP TABLE IF EXISTS {self.qschema}.{staging}")
            self.con.execute(
                f"CREATE TABLE {self.qschema}.{staging} AS "
                f"SELECT {projection} FROM {self.qschema}.{table}"
            )
            self.con.execute(f"DROP TABLE {self.qschema}.{table}")
            self.con.execute(
                f"ALTER TABLE {self.qschema}.{staging} RENAME TO {table}"
            )
            logger.warning("landing.%s: rebuilt to widen %s",
                           table, sorted(remaining))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"landing.{table}: could not widen {sorted(remaining)} either "
                f"in place or by rebuild: {exc}. The batch was not written. "
                f"Fix the source values, or drop the table and re-ingest."
            ) from exc

    def _reconcile_columns(self, table: str, df: pd.DataFrame) -> None:
        existing = set(self._table_columns(table))
        new_columns = [c for c in df.columns if c not in existing]
        if not new_columns:
            return
        if not self.evolve_schema:
            raise ValueError(
                f"landing.{table}: frozen schema, but the source added columns "
                f"{new_columns}. Set LANDING_EVOLVE_SCHEMA=true to allow, or fix "
                f"the source workbook."
            )
        described = self.con.execute("DESCRIBE SELECT * FROM _incoming").fetchall()
        types = {row[0]: row[1] for row in described}
        for column in new_columns:
            column_type = types.get(column, "VARCHAR")
            self.con.execute(
                f'ALTER TABLE {self.qschema}.{table} ADD COLUMN "{column}" {column_type}'
            )
            logger.warning(
                "landing.%s: new source column %r added as %s", table, column, column_type
            )

    # ── reading ────────────────────────────────────────────────────────────

    def current_rows_sql(self, table: str) -> str:
        """
        SQL for the current view of a landing table: for each source file, only
        rows from its most recent ingest, excluding retired files.

        This is the query raw_v2_* models will use in phase 2.
        """
        return f"""
        WITH latest AS (
            SELECT source_path, MAX(registered_at) AS registered_at
            FROM {self.qschema}.{self.registry}
            WHERE status = 'ingested'
              AND source_path NOT IN (
                  SELECT source_path FROM {self.qschema}.{self.registry}
                  WHERE status = 'retired'
              )
            GROUP BY source_path
        ),
        current_files AS (
            SELECT r.source_path, r.file_sha256
            FROM {self.qschema}.{self.registry} r
            JOIN latest l
              ON r.source_path = l.source_path
             AND r.registered_at = l.registered_at
            WHERE r.status = 'ingested'
        )
        SELECT t.*
        FROM {self.qschema}.{table} t
        JOIN current_files c
          ON t._source_path = c.source_path
         AND t._file_sha256 = c.file_sha256
        """

    def current_rows(self, table: str) -> pd.DataFrame:
        return self.con.execute(self.current_rows_sql(table)).df()

    def summary(self) -> pd.DataFrame:
        """
        Current vs total row counts per landing table, and how many source
        files each is built from. This is the ingestion layer's output
        contract made visible -- what `--verify` prints.
        """
        tables = [
            r[0] for r in self.con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_catalog = ? AND table_schema = ? ORDER BY table_name",
                [self._catalog_name, self.schema],
            ).fetchall()
            if r[0] not in OBSERVABILITY_TABLES
        ]

        rows = []
        for table in tables:
            total = self.con.execute(
                f"SELECT COUNT(*) FROM {self.qschema}.{table}"
            ).fetchone()[0]
            current = self.con.execute(
                f"SELECT COUNT(*) FROM ({self.current_rows_sql(table)})"
            ).fetchone()[0]
            files = self.con.execute(
                f"SELECT COUNT(DISTINCT _source_path) FROM ({self.current_rows_sql(table)})"
            ).fetchone()[0]
            rows.append({
                "table": table,
                "current_rows": current,
                "total_rows": total,
                "superseded": total - current,
                "source_files": files,
            })
        return pd.DataFrame(rows)

    def observability_summary(self) -> pd.DataFrame:
        """
        Row counts for the landing tables that hold observability rather than
        source data.

        Reported separately from summary() because they have no _source_path
        and so cannot be described in terms of current/superseded rows. They
        are listed at all because their EXISTENCE is what matters: `sqlmesh
        plan` builds meta views over audit_results/audit_failures and fails
        outright if they are missing, and "empty" looks identical to "absent"
        unless something says so.
        """
        rows = []
        for table in sorted(OBSERVABILITY_TABLES):
            try:
                n = self.con.execute(
                    f"SELECT COUNT(*) FROM {self.qschema}.{table}"
                ).fetchone()[0]
                rows.append({"table": table, "rows": n, "exists": True})
            except Exception:
                rows.append({"table": table, "rows": None, "exists": False})
        return pd.DataFrame(rows)

    def registry_summary(self) -> pd.DataFrame:
        return self.con.execute(
            f"SELECT status, COUNT(*) AS files, COALESCE(SUM(row_count), 0) AS rows "
            f"FROM {self.qschema}.{self.registry} GROUP BY status ORDER BY status"
        ).df()

    def stats(self) -> Dict[str, int]:
        rows = self.con.execute(
            f"SELECT status, COUNT(*), COALESCE(SUM(row_count), 0) "
            f"FROM {self.qschema}.{self.registry} WHERE batch_id = ? GROUP BY status",
            [self.batch_id],
        ).fetchall()
        return {status: {"files": n, "rows": total} for status, n, total in rows}