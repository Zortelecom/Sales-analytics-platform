"""
Base class for extracting data from Excel Table objects.

Fixes applied
─────────────
1. wb.close() moved into a finally block so the workbook file handle is
   always released, even when an exception occurs mid-sheet.  Previously,
   any exception before the end of extract_single_file() would leave the
   file open — causing PermissionError on the next run (Windows) and
   descriptor leaks on all platforms.

2. validate_sheet_name() is now a proper method on the base class with a
   pass-all default implementation, replacing the hasattr() duck-typing
   pattern.  Subclasses that don't override it get pass-all behaviour
   (same as before), but a subclass that forgets the override no longer
   silently processes every sheet — it just uses the documented default.
"""
from __future__ import annotations

import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Set

import openpyxl
import pandas as pd

logger = logging.getLogger(__name__)


class BaseExcelExtractor:
    """
    Base class for extracting data from Excel Table objects.
    Ensures consistent metadata and error handling across all sources.
    """

    def __init__(self, batch_id: str) -> None:
        self.batch_id    = batch_id
        self.ingestion_ts = datetime.now(timezone.utc)

    # ── Sheet-level hook ────────────────────────────────────────────────────

    def validate_sheet_name(self, sheet_name: str, file_path: Path) -> bool:
        """
        Return True if this sheet should be processed.

        Default implementation accepts every sheet.  Override in subclasses
        to add source-specific filtering (see SalesExtractor for an example).

        Args:
            sheet_name: Name of the sheet as it appears in the workbook.
            file_path:  Path to the workbook being processed.
        """
        return True

    # ── Workbook access ─────────────────────────────────────────────────────

    def _get_workbook(self, file_path: Path) -> openpyxl.Workbook:
        """
        Open a workbook safely.

        read_only=False is required to access Excel Table objects via the
        .tables attribute — read-only mode does not expose them.
        """
        try:
            return openpyxl.load_workbook(
                file_path,
                data_only=True,
                read_only=False,
                keep_links=False,
            )
        except Exception as exc:
            logger.error("Could not open workbook %s: %s", file_path.name, exc)
            raise

    # ── Single-file extraction ───────────────────────────────────────────────

    def extract_single_file(
        self,
        file_path: Path,
        table_prefix: str,
        exclude_sheets: List[str],
        required_columns: Optional[Set[str]] = None,
    ) -> pd.DataFrame:
        """
        Iterate through all sheets in *file_path* and collect every Excel
        Table whose name starts with *table_prefix*.

        The workbook is always closed in a finally block so callers don't
        need to worry about resource cleanup on exception paths.
        """
        wb = self._get_workbook(file_path)
        file_frames: List[pd.DataFrame] = []

        try:
            for sheet_name in wb.sheetnames:

                # Sheet-level exclusions
                if (sheet_name.lower() in exclude_sheets
                        or sheet_name.lower().startswith("synthese")):
                    logger.debug("Skipping excluded sheet: %s", sheet_name)
                    continue

                if not self.validate_sheet_name(sheet_name, file_path):
                    continue

                ws = wb[sheet_name]

                if not getattr(ws, "tables", None):
                    logger.debug("Sheet %s has no tables, skipping", sheet_name)
                    continue

                # openpyxl exposes tables as a dict-like in recent versions
                # and as a list in older ones — handle both.
                try:
                    table_names = list(ws.tables.keys()) if hasattr(ws.tables, "keys") else list(ws.tables)
                except AttributeError:
                    table_names = list(ws.tables)

                for table_name in table_names:
                    if not table_name.startswith(table_prefix):
                        continue

                    try:
                        table = ws.tables[table_name]
                        logger.info("Found table: %s in sheet %s", table_name, sheet_name)

                        df = self._table_to_df(ws, table.ref)
                        if df is None or df.empty:
                            continue

                        if required_columns:
                            current_cols = {str(c).lower().strip() for c in df.columns}
                            missing      = required_columns - current_cols
                            if missing:
                                logger.error(
                                    "SCHEMA ERROR in File: '%s' | Sheet: '%s' | Table: '%s'",
                                    file_path.name, sheet_name, table_name,
                                )
                                logger.error("   Present columns: %s", sorted(current_cols))
                                logger.error("   Missing columns: %s", sorted(missing))
                                continue  # skip this table, don't pollute the dataset

                        # Standard provenance metadata
                        df["source_file"]        = file_path.name
                        df["sheet_name"]         = sheet_name
                        df["table_name"]         = table_name
                        df["ingestion_ts"]       = self.ingestion_ts
                        df["ingestion_batch_id"] = self.batch_id

                        # Coerce everything except the timestamp to str for the
                        # Raw layer — prevents type-inference surprises in DuckDB.
                        for col in df.columns:
                            if col != "ingestion_ts":
                                df[col] = df[col].astype(str)

                        file_frames.append(df)
                        logger.info("Extracted %d rows from table %s", len(df), table_name)

                    except Exception as exc:
                        logger.error(
                            "Error processing table %s: %s", table_name, exc, exc_info=True
                        )
                        continue

        finally:
            # Always release the file handle — critical on Windows where an
            # open workbook prevents subsequent writes to the same file.
            wb.close()

        if not file_frames:
            logger.warning("No tables extracted from %s", file_path.name)

        return pd.concat(file_frames, ignore_index=True) if file_frames else pd.DataFrame()

    # ── Table → DataFrame ───────────────────────────────────────────────────

    def _table_to_df(self, ws, table_ref: str) -> Optional[pd.DataFrame]:
        """Convert an openpyxl table range to a pandas DataFrame."""
        try:
            data = list(ws[table_ref])
            if not data:
                return None

            # Build header, filling blank cells with a placeholder.
            headers = [
                str(cell.value).strip() if cell.value else f"col_{i}"
                for i, cell in enumerate(data[0])
            ]

            values = [[cell.value for cell in row] for row in data[1:]]
            df = pd.DataFrame(values, columns=headers)
            df.dropna(how="all", inplace=True)

            if df.empty:
                logger.warning("Table at %s has no data rows", table_ref)
                return None

            return df

        except Exception as exc:
            logger.error(
                "Error converting table at %s: %s", table_ref, exc, exc_info=True
            )
            return None

    # ── Directory-level extraction ───────────────────────────────────────────

    def extract_from_directory(
        self,
        directory: Path,
        table_prefix: str,
        exclude_sheets: List[str] = None,
        file_pattern: str = "*.xlsx",
        required_columns: Optional[Set[str]] = None,
    ) -> pd.DataFrame:
        """
        Scan *directory* for Excel files matching *file_pattern* and extract
        all tables whose names start with *table_prefix*.

        Individual file failures are logged and skipped so a single corrupt
        file does not abort the whole batch.
        """
        frames: List[pd.DataFrame] = []
        exclude_sheets = [s.lower() for s in (exclude_sheets or [])]

        if not directory.exists():
            logger.warning("Directory %s does not exist.", directory)
            return pd.DataFrame()

        matching_files = sorted(directory.glob(file_pattern))

        if not matching_files:
            logger.warning(
                "No files matching pattern '%s' in %s", file_pattern, directory
            )
            return pd.DataFrame()

        for file_path in matching_files:
            if file_path.name.startswith("~$"):
                continue  # skip Excel temporary lock files

            logger.info("Processing %s file: %s", table_prefix, file_path.name)
            try:
                df_file = self.extract_single_file(
                    file_path, table_prefix, exclude_sheets, required_columns
                )
                if not df_file.empty:
                    frames.append(df_file)
            except Exception as exc:
                logger.error(
                    "Failed to process %s: %s", file_path.name, exc, exc_info=True
                )
                continue  # keep going; don't abort the batch

        if not frames:
            logger.warning("No data extracted from any files in %s", directory)
            return pd.DataFrame()

        return pd.concat(frames, ignore_index=True)