#!/usr/bin/env python3
"""
DuckLake to Serving Layer Sync
Supports two sync strategies:

  FILE-SWAP (default)
    Build serving.db in a temp file, then atomically rename it.
    Improved: Arrow streaming replaces the Pandas round-trip;
    DuckDB's native chunked transfer replaces LIMIT/OFFSET batching.

  QUACK (opt-in, DuckDB >= v1.5.2 beta)
    A persistent Quack server wraps serving.db.  The sync process
    ATTACHes to the live server and rewrites each mart table with
    CREATE OR REPLACE TABLE AS SELECT from the DuckLake source.
    No temp file, no rename, no BI outage, no Python memory overhead.
    See: https://duckdb.org/quack/

Export services (optional, async, event-triggered)
---------------------------------------------------
CSV and Parquet exports live in serving/export/ as independent services.
Pass an ExportEventBus to ServingLayerSync; it fires a SyncCompletedEvent
when sync finishes and the bus dispatches to each registered exporter
concurrently.

    from serving.export import ExportEventBus, CsvExporter, ParquetExporter

    bus = (
        ExportEventBus()
        .subscribe(CsvExporter("data/exports/csv/dev"))
        .subscribe(ParquetExporter("data/exports/parquet/dev"))
    )
    sync = ServingLayerSync(config, export_bus=bus)
    sync.sync()          # exports fire automatically when sync completes

If no bus is passed, exports are simply skipped. This makes them truly
optional: registering nothing disables exports with no config changes.
"""

import shutil
import logging
from pathlib import Path
from datetime import datetime
from time import sleep
from typing import List, Tuple, Optional, Dict
import duckdb
from .config import ServingConfig, QuackConfig
from .export.event_bus import ExportEventBus
from .export.events import SyncCompletedEvent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Quack Server lifecycle helper
# ---------------------------------------------------------------------------

class QuackServer:
    """
    Context manager that starts a DuckDB Quack server backed by serving.db
    and stops it on exit.

    Intended for use in the Dagster pipeline (as a long-lived resource) or
    standalone via `cli.py serve`.  The sync process itself does NOT own the
    server; it only connects as a client.

    Example (standalone)::

        from .config import QuackConfig
        with QuackServer(serving_path="data/warehouse/serving.db",
                         quack=QuackConfig(token="s3cr3t")) as srv:
            print(f"Quack server listening on {srv.uri}")

    Note: Quack is in beta. Install with:
        INSTALL quack FROM core_nightly;
    """

    def __init__(self, serving_path: str, quack: QuackConfig) -> None:
        self.serving_path = serving_path
        self.quack = quack
        self._conn: Optional[duckdb.DuckDBPyConnection] = None

    @property
    def uri(self) -> str:
        return self.quack.uri

    def start(self) -> None:
        logger.warning('''Quack is in beta (DuckDB ≥ v1.5.2). Stable release: 
            DuckDB v2.0 Sep 2026. Use file-swap in production.''')
        logger.info("Starting Quack server on %s backed by %s ...",
                    self.quack.uri, self.serving_path)
        Path(self.serving_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(self.serving_path)
        _ensure_quack_extension(self._conn)
        _ensure_ducklake_extension(self._conn)
        self._conn.execute(
            f"CALL quack_serve('{self.quack.uri}', token := '{self.quack.token}')"
        )
        logger.info("Quack server listening on %s", self.quack.uri)

    def stop(self) -> None:
        if self._conn:
            try:
                self._conn.execute(f"CALL quack_stop('{self.quack.uri}')")
                logger.info("Quack server stopped.")
            except Exception as exc:
                logger.warning("Error stopping Quack server: %s", exc)
            finally:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> "QuackServer":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Extension helpers
# ---------------------------------------------------------------------------

def _ensure_ducklake_extension(conn: duckdb.DuckDBPyConnection) -> None:
    try:
        conn.execute("LOAD ducklake;")
    except duckdb.CatalogException:
        logger.warning("DuckLake extension not found -- installing ...")
        try:
            conn.execute("INSTALL ducklake; LOAD ducklake;")
        except Exception as exc:
            raise RuntimeError(f"Failed to install DuckLake: {exc}") from exc


def _ensure_quack_extension(conn: duckdb.DuckDBPyConnection) -> None:
    try:
        conn.execute("LOAD quack;")
    except duckdb.CatalogException:
        logger.warning(
            "Quack extension not found -- installing from core_nightly ...\n"
            "  Quack is in beta (DuckDB >= v1.5.2). Stable: DuckDB v2.0 (Sept 2026)."
        )
        try:
            conn.execute("INSTALL quack FROM core_nightly; LOAD quack;")
        except Exception as exc:
            raise RuntimeError(
                f"Failed to install Quack: {exc}. "
                "Disable Quack mode by setting ServingConfig.quack = None."
            ) from exc


# ---------------------------------------------------------------------------
# Main sync class
# ---------------------------------------------------------------------------

class ServingLayerSync:
    """
    Syncs DuckLake marts tables to a serving DuckDB database for BI tools.

    Chooses between two strategies based on ServingConfig.quack.
    Export services (CSV, Parquet) are completely decoupled: register them
    on an ExportEventBus and pass it to the constructor.

    Args:
        config:     Serving layer configuration (must be normalised).
        export_bus: Optional event bus with registered exporters.
                    If None, no exports happen -- no CSV, no Parquet, no error.
    """

    def __init__(
        self,
        config: Optional[ServingConfig] = None,
        export_bus: Optional[ExportEventBus] = None,
    ) -> None:
        self.config = config or ServingConfig()
        self.export_bus = export_bus  # None = exports disabled

        if not self.config.marts_schema:
            raise ValueError(
                "ServingConfig.normalize() must be called before creating ServingLayerSync."
            )

        self.sync_metadata: List[dict] = []
        self.failed_tables: List[Tuple[str, str]] = []

    # =========================================================================
    # Public API
    # =========================================================================

    def sync(self, dry_run: bool = False) -> Dict:
        """
        Run the sync.  Dispatches to Quack or file-swap strategy, then fires
        a SyncCompletedEvent so registered exporters run asynchronously.

        Args:
            dry_run: Report what would be synced without making changes.

        Returns:
            Summary dict with status, timing, per-table results, and export results.
        """
        mode = "quack" if self.config.quack_enabled else "file-swap"
        logger.info("=" * 70)
        logger.info(
            "Starting serving sync | env=%s | schema=%s | mode=%s",
            self.config.environment, self.config.marts_schema, mode,
        )
        logger.info("=" * 70)

        ducklake_path = Path(self.config.ducklake_path)
        if not ducklake_path.exists():
            raise FileNotFoundError(
                f"DuckLake catalog not found: {self.config.ducklake_path}\n"
                "Run SQLMesh transformations first."
            )

        if self.config.quack_enabled:
            summary = self._sync_quack(dry_run)
        else:
            summary = self._sync_file_swap(dry_run)

        # Fire export event (skipped on dry-run or if no bus registered)
        if not dry_run and self.export_bus and summary.get("status") in ("success", "partial_success"):
            export_results = self._trigger_exports(summary)
            summary["exports"] = export_results

        return summary

    def _trigger_exports(self, summary: Dict) -> Dict:
        """
        Build a SyncCompletedEvent from the sync summary and dispatch it
        to the export bus.

        Uses publish_and_wait so the Dagster asset doesn't complete before
        exports finish.  Switch to publish_background for fire-and-forget.
        """
        succeeded_tables = [
            t for t in summary.get("tables_succeeded_names", [])
        ]
        # Fallback: use table count if per-name list isn't populated
        if not succeeded_tables:
            succeeded_tables = [
                name for name, _ in self.failed_tables  # failed ones
            ]
            # Rebuild from sync_metadata if available
            succeeded_tables = [
                m["table"] for m in self.sync_metadata
                if m.get("status") == "success"
            ]

        event = SyncCompletedEvent(
            environment=self.config.environment,
            serving_path=self.config.serving_path,
            bi_schema=self.config.bi_schema,
            tables=succeeded_tables,
            mode=summary.get("mode", "unknown"),
            row_counts={m["table"]: m.get("row_count", 0) for m in self.sync_metadata},
        )

        try:
            results = self.export_bus.publish_and_wait(event)
            return {fmt: str(r) for fmt, r in results.items()}
        except TimeoutError as exc:
            logger.warning("Exports timed out: %s", exc)
            return {"status": "timeout", "error": str(exc)}
        except Exception as exc:
            logger.error("Export dispatch failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    # =========================================================================
    # QUACK STRATEGY
    # =========================================================================

    def _sync_quack(self, dry_run: bool) -> Dict:
        """
        Quack-powered sync: DuckLake -> Quack server without temp files.

        Data flow:
            [DuckLake catalog]  --ATTACH (TYPE DUCKLAKE)--+
                                                          +-- [in-memory conn]
            [Quack server]      --ATTACH (quack:...)------+
                                       |
                            CREATE OR REPLACE TABLE
                            <quack_alias>.bi.<table> AS
                            SELECT * FROM <ducklake_alias>.marts.<table>
        """
        if dry_run:
            logger.info("DRY RUN MODE -- no changes will be made.")

        conn = duckdb.connect(":memory:")
        try:
            _ensure_ducklake_extension(conn)
            _ensure_quack_extension(conn)

            conn.execute(
                f"ATTACH '{self.config.ducklake_path}' "
                f"AS {self.config.source_catalog_alias} (TYPE DUCKLAKE)"
            )
            logger.info("Attached DuckLake catalog as '%s'.", self.config.source_catalog_alias)

            self._validate_source_schema(conn)
            tables = self._discover_marts_tables(conn)

            if not tables:
                raise ValueError(
                    f"No tables found in schema '{self.config.marts_schema}'. "
                    "Have you run SQLMesh transformations?"
                )
            logger.info("Found %d table(s): %s", len(tables), [t[1] for t in tables])

            if dry_run:
                return self._dry_run_report(conn, tables)

            q = self.config.quack
            conn.execute(
                f"CREATE OR REPLACE SECRET {q.secret_name} "
                f"(TYPE quack, TOKEN '{q.token}')"
            )
            conn.execute(f"ATTACH '{q.uri}' AS {q.catalog_alias}")
            logger.info("Attached Quack server as '%s' (%s).", q.catalog_alias, q.uri)

            self._setup_serving_schema_quack(conn)

            sync_start = datetime.now()
            self._sync_all_tables_quack(conn, tables)

            if self.config.apply_views:
                self._apply_bi_views_quack(conn)

            success_count = len(tables) - len(self.failed_tables)
            failure_rate = len(self.failed_tables) / len(tables) if tables else 0

            if failure_rate >= 0.5:
                raise RuntimeError(
                    f"Sync aborted: {len(self.failed_tables)}/{len(tables)} tables failed.\n"
                    + "\n".join(f"  - {t}: {e}" for t, e in self.failed_tables)
                )

            duration = (datetime.now() - sync_start).total_seconds()

            return {
                "status": "success" if not self.failed_tables else "partial_success",
                "mode": "quack",
                "duration_seconds": duration,
                "tables_total": len(tables),
                "tables_succeeded": success_count,
                "tables_failed": len(self.failed_tables),
                "failed_tables": [t for t, _ in self.failed_tables],
                "serving_path": self.config.serving_path,
                "quack_uri": self.config.quack.uri,
            }

        finally:
            conn.close()

    def _setup_serving_schema_quack(self, conn: duckdb.DuckDBPyConnection) -> None:
        alias = self.config.quack.catalog_alias
        bi = self.config.bi_schema
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {alias}.{bi}")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {alias}.{bi}._sync_log (
                sync_timestamp TIMESTAMP NOT NULL,
                source_table   VARCHAR   NOT NULL,
                target_table   VARCHAR   NOT NULL,
                row_count      BIGINT    NOT NULL,
                duration_ms    BIGINT    NOT NULL,
                status         VARCHAR   NOT NULL,
                error_message  VARCHAR
            )
        """)

    def _sync_all_tables_quack(self, conn, tables):
        logger.info("Syncing %d table(s) via Quack ...", len(tables))
        for idx, (schema, table) in enumerate(tables, 1):
            logger.info("  [%d/%d] %s", idx, len(tables), table)
            if self._should_skip_table(table):
                logger.info("    Skipped.")
                continue
            try:
                self._sync_table_quack(conn, schema, table)
            except Exception as exc:
                logger.error("    Failed: %s", exc)
                self.failed_tables.append((table, str(exc)))
                self._log_sync_failure_quack(conn, schema, table, str(exc))

    def _sync_table_quack(self, conn, source_schema, table):
        start = datetime.now()
        catalog = self.config.source_catalog_alias
        alias = self.config.quack.catalog_alias
        bi = self.config.bi_schema
        source_full = f"{catalog}.{source_schema}.{table}"
        target_full = f"{alias}.{bi}.{table}"

        conn.execute(f"""
            CREATE OR REPLACE TABLE {target_full} AS
            SELECT * FROM {source_full}
        """)

        row_count = conn.execute(f"SELECT COUNT(*) FROM {target_full}").fetchone()[0]
        duration_ms = int((datetime.now() - start).total_seconds() * 1_000)

        conn.execute(f"""
            INSERT INTO {alias}.{bi}._sync_log
                (sync_timestamp, source_table, target_table, row_count, duration_ms, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [datetime.now(), f"{source_schema}.{table}", target_full,
              row_count, duration_ms, "success"])

        self.sync_metadata.append({"table": table, "row_count": row_count, "status": "success"})
        logger.info("    %s rows in %d ms", f"{row_count:,}", duration_ms)

    def _log_sync_failure_quack(self, conn, schema, table, error):
        alias = self.config.quack.catalog_alias
        bi = self.config.bi_schema
        try:
            conn.execute(f"""
                INSERT INTO {alias}.{bi}._sync_log
                    (sync_timestamp, source_table, target_table, row_count,
                     duration_ms, status, error_message)
                VALUES (?, ?, ?, 0, 0, 'error', ?)
            """, [datetime.now(), f"{schema}.{table}", f"{alias}.{bi}.{table}", error[:500]])
        except Exception as exc:
            logger.error("Failed to log Quack error for %s: %s", table, exc)

    def _apply_bi_views_quack(self, conn):
        sql_path = Path(self.config.views_template_path)
        if not sql_path.exists():
            raise FileNotFoundError(f"BI views template not found: {sql_path}")
        logger.info("Applying BI views on Quack server ...")
        alias = self.config.quack.catalog_alias
        sql_content = sql_path.read_text(encoding="utf-8")
        statements = [s.strip() for s in sql_content.split(";") if s.strip()]
        conn.execute(f"USE {alias}.main")
        try:
            for stmt in statements:
                conn.execute(stmt)
            logger.info("Created %d BI view(s) on Quack server.", len(statements))
        finally:
            conn.execute("USE memory.main")

    # =========================================================================
    # FILE-SWAP STRATEGY
    # =========================================================================

    def _sync_file_swap(self, dry_run: bool) -> Dict:
        """
        File-swap sync with Arrow streaming (no pandas, no LIMIT/OFFSET batching).
        """
        if dry_run:
            logger.info("DRY RUN MODE -- no changes will be made.")

        source = duckdb.connect(":memory:")
        try:
            _ensure_ducklake_extension(source)
            source.execute(
                f"ATTACH '{self.config.ducklake_path}' "
                f"AS {self.config.source_catalog_alias} (TYPE DUCKLAKE)"
            )

            self._validate_source_schema(source)
            tables = self._discover_marts_tables(source)

            if not tables:
                raise ValueError(
                    f"No tables found in schema '{self.config.marts_schema}'. "
                    "Have you run SQLMesh transformations?"
                )
            logger.info("Found %d table(s): %s", len(tables), [t[1] for t in tables])

            if dry_run:
                return self._dry_run_report(source, tables)

            temp_path = Path(self.config.temp_path)
            if temp_path.exists():
                temp_path.unlink()

            logger.info("Creating temp serving database: %s", temp_path)
            target = duckdb.connect(str(temp_path))

            try:
                self._setup_serving_schema_file(target)
                sync_start = datetime.now()
                self._sync_all_tables_file(source, target, tables)

                if self.config.apply_views:
                    self._apply_bi_views(target)

                success_count = len(tables) - len(self.failed_tables)
                failure_rate = len(self.failed_tables) / len(tables) if tables else 0

                if failure_rate >= 0.5:
                    raise RuntimeError(
                        f"Sync aborted: {len(self.failed_tables)}/{len(tables)} failed."
                    )

                target.close()
                self._atomic_swap()
                duration = (datetime.now() - sync_start).total_seconds()

                return {
                    "status": "success" if not self.failed_tables else "partial_success",
                    "mode": "file-swap",
                    "duration_seconds": duration,
                    "tables_total": len(tables),
                    "tables_succeeded": success_count,
                    "tables_failed": len(self.failed_tables),
                    "failed_tables": [t for t, _ in self.failed_tables],
                    "serving_path": self.config.serving_path,
                }

            except Exception:
                target.close()
                if temp_path.exists():
                    temp_path.unlink()
                raise

        finally:
            source.close()

    def _setup_serving_schema_file(self, target):
        bi = self.config.bi_schema
        target.execute(f"CREATE SCHEMA IF NOT EXISTS {bi}")
        target.execute(f"""
            CREATE TABLE IF NOT EXISTS {bi}._sync_log (
                sync_timestamp TIMESTAMP NOT NULL,
                source_table   VARCHAR   NOT NULL,
                target_table   VARCHAR   NOT NULL,
                row_count      BIGINT    NOT NULL,
                duration_ms    BIGINT    NOT NULL,
                status         VARCHAR   NOT NULL,
                error_message  VARCHAR
            )
        """)

    def _sync_all_tables_file(self, source, target, tables):
        logger.info("Syncing %d table(s) via file-swap ...", len(tables))
        catalog = self.config.source_catalog_alias
        for idx, (schema, table) in enumerate(tables, 1):
            logger.info("  [%d/%d] %s", idx, len(tables), table)
            if self._should_skip_table(table):
                logger.info("    Skipped.")
                continue
            try:
                self._sync_table_file(source, target, schema, table, catalog)
            except Exception as exc:
                logger.error("    Failed: %s", exc)
                self.failed_tables.append((table, str(exc)))
                self._log_sync_failure_file(target, schema, table, str(exc))

    def _sync_table_file(self, source, target, source_schema, table, catalog_alias):
        """Arrow streaming: no pandas, no LIMIT/OFFSET."""
        start = datetime.now()
        source_full = f"{catalog_alias}.{source_schema}.{table}"
        target_table = f"{self.config.bi_schema}.{table}"

        arrow_tbl = source.execute(f"SELECT * FROM {source_full}").arrow()
        target.register("_arrow_tmp", arrow_tbl)
        target.execute(f"CREATE TABLE {target_table} AS SELECT * FROM _arrow_tmp")
        target.unregister("_arrow_tmp")

        row_count = target.execute(f"SELECT COUNT(*) FROM {target_table}").fetchone()[0]
        duration_ms = int((datetime.now() - start).total_seconds() * 1_000)

        target.execute(f"""
            INSERT INTO {self.config.bi_schema}._sync_log
                (sync_timestamp, source_table, target_table, row_count, duration_ms, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [datetime.now(), f"{source_schema}.{table}", target_table,
              row_count, duration_ms, "success"])

        self.sync_metadata.append({"table": table, "row_count": row_count, "status": "success"})
        logger.info("    %s rows in %d ms", f"{row_count:,}", duration_ms)

    def _log_sync_failure_file(self, target, schema, table, error):
        try:
            target.execute(f"""
                INSERT INTO {self.config.bi_schema}._sync_log
                    (sync_timestamp, source_table, target_table, row_count,
                     duration_ms, status, error_message)
                VALUES (?, ?, ?, 0, 0, 'error', ?)
            """, [datetime.now(), f"{schema}.{table}",
                  f"{self.config.bi_schema}.{table}", error[:500]])
        except Exception as exc:
            logger.error("Failed to log error for %s: %s", table, exc)

    def _apply_bi_views(self, target):
        sql_path = Path(self.config.views_template_path)
        if not sql_path.exists():
            raise FileNotFoundError(f"BI views template not found: {sql_path}")
        logger.info("Applying BI views ...")
        sql_content = sql_path.read_text(encoding="utf-8")
        statements = [s.strip() for s in sql_content.split(";") if s.strip()]
        for stmt in statements:
            try:
                target.execute(stmt)
            except Exception as exc:
                raise RuntimeError(f"BI view creation failed: {exc}") from exc
        logger.info("Created %d BI view(s).", len(statements))

    def _atomic_swap(self):
        serving = Path(self.config.serving_path)
        temp = Path(self.config.temp_path)
        backup = Path(self.config.serving_path.replace(".db", "_backup.db"))

        if serving.exists():
            if backup.exists():
                backup.unlink()
            shutil.move(str(serving), str(backup))

        for attempt in range(3):
            try:
                shutil.move(str(temp), str(serving))
                break
            except PermissionError as exc:
                if attempt < 2:
                    sleep(0.5)
                else:
                    if backup.exists():
                        shutil.move(str(backup), str(serving))
                    raise RuntimeError(f"Swap failed after 3 attempts: {exc}") from exc

        if backup.exists():
            backup.unlink()
        logger.info("Atomic swap complete: %s", serving)

    # =========================================================================
    # Shared helpers
    # =========================================================================

    def _discover_marts_tables(self, conn) -> List[Tuple[str, str]]:
        try:
            result = conn.execute("""
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema = ?
                ORDER BY table_name
            """, [self.config.marts_schema]).fetchall()

            if not result:
                schemas = conn.execute(
                    f"SELECT DISTINCT table_schema FROM "
                    f"{self.config.source_catalog_alias}.information_schema.tables"
                ).fetchall()
                logger.info("Available schemas: %s", [s[0] for s in schemas])

            logger.info("Discovered %d table(s) in '%s'.", len(result), self.config.marts_schema)
            return result
        except duckdb.CatalogException:
            logger.error("Failed to query schema '%s'.", self.config.marts_schema)
            return []

    def _validate_source_schema(self, conn) -> None:
        schemas = {s[0] for s in conn.execute(
            "SELECT schema_name FROM information_schema.schemata"
        ).fetchall()}
        if self.config.marts_schema not in schemas:
            raise ValueError(
                f"Schema '{self.config.marts_schema}' not found.\n"
                f"Available: {', '.join(sorted(schemas))}"
            )

    def _should_skip_table(self, table: str) -> bool:
        if self.config.include_tables:
            return table not in self.config.include_tables
        if self.config.exclude_tables:
            return table in self.config.exclude_tables
        return False

    def _dry_run_report(self, conn, tables) -> Dict:
        logger.info("DRY RUN REPORT")
        catalog = self.config.source_catalog_alias
        total_rows = 0
        for schema, table in tables:
            if self._should_skip_table(table):
                logger.info("  SKIP: %s", table)
                continue
            try:
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {catalog}.{schema}.{table}"
                ).fetchone()[0]
                total_rows += count
                logger.info("  Would sync: %-40s  %10s rows", table, f"{count:,}")
            except Exception as exc:
                logger.info("  Cannot read: %s -- %s", table, exc)
        logger.info("Total: %d tables, %s rows", len(tables), f"{total_rows:,}")
        return {"status": "dry_run", "tables_count": len(tables), "total_rows": total_rows}

    def validate_serving_db(self) -> bool:
        serving_path = Path(self.config.serving_path)
        if not serving_path.exists():
            logger.warning("Serving database not found: %s", serving_path)
            return False
        try:
            conn = duckdb.connect(str(serving_path), read_only=True)
            tables = conn.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = ? AND table_name = '_sync_log'
            """, [self.config.bi_schema]).fetchall()
            if not tables:
                conn.close()
                return False
            result = conn.execute(
                f"SELECT MAX(sync_timestamp) FROM {self.config.bi_schema}._sync_log"
            ).fetchone()
            conn.close()
            if result and result[0]:
                age_s = (datetime.now() - result[0]).total_seconds()
                logger.info("Serving DB last synced %.1f hours ago.", age_s / 3600)
                return age_s < 86_400
            return False
        except Exception as exc:
            logger.error("Validation error: %s", exc)
            return False

    def get_sync_stats(self) -> Optional[Dict]:
        serving_path = Path(self.config.serving_path)
        if not serving_path.exists():
            return None
        try:
            conn = duckdb.connect(str(serving_path), read_only=True)
            row = conn.execute(f"""
                SELECT
                    MAX(sync_timestamp),
                    COUNT(*),
                    SUM(status = 'success'),
                    SUM(status = 'error'),
                    SUM(row_count),
                    AVG(duration_ms)
                FROM {self.config.bi_schema}._sync_log
            """).fetchone()
            conn.close()
            if row:
                return {
                    "last_sync": row[0], "total_syncs": row[1],
                    "successful_syncs": row[2], "failed_syncs": row[3],
                    "total_rows": row[4], "avg_duration_ms": row[5],
                }
        except Exception as exc:
            logger.error("Failed to get sync stats: %s", exc)
        return None

    def get_table_stats(self, conn, schema, table, catalog=None) -> Dict:
        try:
            full = f"{catalog}.{schema}.{table}" if catalog else f"{schema}.{table}"
            row_count = conn.execute(f"SELECT COUNT(*) FROM {full}").fetchone()[0]
            col_count = conn.execute("""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema = ? AND table_name = ?
            """, [schema, table]).fetchone()[0]
            return {"row_count": row_count, "column_count": col_count, "status": "success"}
        except Exception as exc:
            return {"row_count": 0, "column_count": 0, "status": "error", "error": str(exc)}
