"""
Abstract base class for all export services.

Each concrete exporter (CSV, Parquet, …) inherits from BaseExporter,
implements `export()`, and is registered with ExportEventBus.

Design goals:
- async: each exporter's `export()` is a coroutine so CSV and Parquet
  exports run concurrently (asyncio.gather) without blocking each other.
- decoupled: exporters know nothing about the sync internals; they only
  receive a SyncCompletedEvent and a source DuckDB connection.
- optional: exporters are registered explicitly; leaving one out disables
  that format with no config changes elsewhere.
- testable: mock or subclass BaseExporter to test the event bus in isolation.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .events import SyncCompletedEvent

logger = logging.getLogger(__name__)


@dataclass
class ExportResult:
    """
    Outcome of a single exporter run.

    Attributes:
        format:       Export format label (e.g. "csv", "parquet").
        exported:     Number of tables successfully written.
        failed:       Number of tables that raised an error.
        output_path:  Directory where files were written.
        duration_ms:  Wall-clock time of the entire export run.
        errors:       Per-table error messages for failed tables.
        skipped:      Table names skipped due to config filters.
    """

    format: str
    exported: int = 0
    failed: int = 0
    output_path: Optional[str] = None
    duration_ms: int = 0
    errors: dict = field(default_factory=dict)   # table → error message
    skipped: List[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.failed == 0:
            return "success"
        if self.exported > 0:
            return "partial"
        return "error"

    def __str__(self) -> str:
        return (
            f"ExportResult({self.format}: "
            f"{self.exported} ok, {self.failed} failed, "
            f"{self.duration_ms} ms)"
        )


class BaseExporter(ABC):
    """
    Abstract export service.

    Subclasses must implement:
        `export_table(conn, table_name, output_dir)` — write one table to disk.

    The base class handles:
        - Per-table error isolation (one failure doesn't abort the rest).
        - Timing and structured ExportResult reporting.
        - Output directory creation and optional pre-run cleanup.
        - Table include/exclude filtering (respects the same lists as sync).
    """

    #: Human-readable label used in logs and ExportResult.
    format: str = "base"

    def __init__(
        self,
        output_path: str,
        include_tables: Optional[List[str]] = None,
        exclude_tables: Optional[List[str]] = None,
        clean_on_run: bool = True,
    ) -> None:
        """
        Args:
            output_path:    Directory where exported files are written.
            include_tables: If set, only these tables are exported.
            exclude_tables: Tables to skip (ignored if include_tables is set).
            clean_on_run:   If True, wipe output_path before each export run
                            so stale files from previous syncs don't accumulate.
        """
        self.output_path = Path(output_path)
        self.include_tables = include_tables
        self.exclude_tables = exclude_tables
        self.clean_on_run = clean_on_run

    # ------------------------------------------------------------------
    # Public coroutine — called by ExportEventBus
    # ------------------------------------------------------------------

    async def export(self, event: SyncCompletedEvent) -> ExportResult:
        """
        Entry point called by the event bus after a sync completes.

        Connects to serving.db (read-only), iterates synced tables,
        delegates to `export_table()`, and returns an ExportResult.

        This method is a coroutine so the bus can run multiple exporters
        concurrently with `asyncio.gather`.  The DuckDB I/O itself is
        synchronous and runs in a thread pool via `asyncio.to_thread`.
        """
        import asyncio
        import duckdb

        logger.info("[%s] Export started — %d table(s) from %s",
                    self.format.upper(), len(event.tables), event.serving_path)

        result = ExportResult(format=self.format, output_path=str(self.output_path))
        start = datetime.now()

        self._prepare_output_dir()

        conn: Optional[duckdb.DuckDBPyConnection] = None
        try:
            # Open the serving DB once; reuse across all table exports.
            conn = duckdb.connect(event.serving_path, read_only=True)

            for table in event.tables:
                if self._should_skip(table):
                    result.skipped.append(table)
                    logger.debug("[%s] Skipping %s", self.format.upper(), table)
                    continue

                try:
                    # Run synchronous DuckDB I/O in a thread pool to stay
                    # non-blocking in the asyncio event loop.
                    await asyncio.to_thread(
                        self.export_table,
                        conn,
                        table,
                        event.bi_schema,
                        self.output_path,
                    )
                    result.exported += 1
                    logger.debug("[%s] ✅ Exported %s", self.format.upper(), table)

                except Exception as exc:
                    result.failed += 1
                    result.errors[table] = str(exc)
                    logger.error("[%s] ❌ Failed to export %s: %s",
                                 self.format.upper(), table, exc)

        except Exception as exc:
            logger.error("[%s] Export aborted: %s", self.format.upper(), exc)
            result.errors["__setup__"] = str(exc)
            result.failed = len(event.tables)
        finally:
            if conn:
                conn.close()

        result.duration_ms = int((datetime.now() - start).total_seconds() * 1_000)
        logger.info("[%s] Export complete — %s", self.format.upper(), result)
        return result

    # ------------------------------------------------------------------
    # To implement in subclasses
    # ------------------------------------------------------------------

    @abstractmethod
    def export_table(
        self,
        conn,           # duckdb.DuckDBPyConnection
        table_name: str,
        bi_schema: str,
        output_dir: Path,
    ) -> None:
        """
        Write a single table to output_dir.

        Args:
            conn:       Read-only connection to serving.db (already open).
            table_name: Name of the table inside `bi_schema`.
            bi_schema:  DuckDB schema containing the table (e.g. "bi").
            output_dir: Directory to write the file into.

        Raise any exception on failure; the base class catches it and
        records it in ExportResult without aborting other tables.
        """

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prepare_output_dir(self) -> None:
        import shutil
        if self.clean_on_run and self.output_path.exists():
            shutil.rmtree(self.output_path)
            logger.debug("[%s] Cleaned output dir: %s", self.format.upper(), self.output_path)
        self.output_path.mkdir(parents=True, exist_ok=True)

    def _should_skip(self, table: str) -> bool:
        if self.include_tables:
            return table not in self.include_tables
        if self.exclude_tables:
            return table in self.exclude_tables
        return False
