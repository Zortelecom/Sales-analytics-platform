from __future__ import annotations
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional
import openpyxl
import pandas as pd

logger = logging.getLogger(__name__)


class BaseExcelExtractor:
    """
    Base class for extracting data from Excel Table objects.
    Ensures consistent metadata and error handling across all sources.
    """

    def __init__(self, batch_id: str):
        self.batch_id = batch_id
        self.ingestion_ts = datetime.now(timezone.utc)

    def _get_workbook(self, file_path: Path):
        """
        Standard way to open workbooks safely.
        IMPORTANT: read_only=False is required to access Excel Table objects (.tables attribute)
        """
        try:
            return openpyxl.load_workbook(file_path, data_only=True, read_only=False)
        except Exception as e:
            logger.error("Could not open workbook %s: %s", file_path.name, e)
            raise

    def extract_single_file(
        self, 
        file_path: Path, 
        table_prefix: str, 
        exclude_sheets: List[str]
    ) -> pd.DataFrame:
        """Logic to iterate through sheets and find Table objects."""
        wb = self._get_workbook(file_path)
        file_frames = []

        for sheet_name in wb.sheetnames:

            if sheet_name.lower() in exclude_sheets or sheet_name.lower().startswith("synthese"):
                logger.debug("Skipping excluded sheet: %s", sheet_name)
                continue
            
            # Allow subclasses to override sheet validation
            if hasattr(self, 'validate_sheet_name'):
                if not self.validate_sheet_name(sheet_name, file_path):
                    continue

            ws = wb[sheet_name]

            # Check if worksheet has tables
            if not hasattr(ws, 'tables') or not ws.tables:
                logger.debug("Sheet %s has no tables, skipping", sheet_name)
                continue

            # Iterate over Excel 'ListObjects' (Tables)
            # Handle different openpyxl versions where ws.tables can be dict-like or list-like
            try:
                table_names = list(ws.tables.keys()) if hasattr(
                    ws.tables, 'keys') else list(ws.tables)
            except AttributeError:
                table_names = list(ws.tables)

            for table_name in table_names:
                if not table_name.startswith(table_prefix):
                    continue

                try:
                    # Get the table object
                    table = ws.tables[table_name]
                    logger.info("Found table: %s in sheet %s",
                                table_name, sheet_name)

                    df = self._table_to_df(ws, table.ref)

                    if df is not None and not df.empty:
                        # Add standard metadata
                        df["source_file"] = file_path.name
                        df["sheet_name"] = sheet_name
                        df["table_name"] = table_name
                        df["ingestion_ts"] = self.ingestion_ts
                        df["ingestion_batch_id"] = self.batch_id

                        # Force all columns (except metadata) to string for the 'Raw' layer
                        # This prevents type inference errors in DuckDB loading
                        for col in df.columns:
                            if col not in ["ingestion_ts"]:
                                df[col] = df[col].astype(str)

                        file_frames.append(df)
                        logger.info("Extracted %d rows from table %s",
                                    len(df), table_name)

                except Exception as e:
                    logger.error("Error processing table %s: %s",
                                 table_name, e, exc_info=True)
                    continue

        wb.close()  # Explicitly close workbook
        if not file_frames:
            logger.warning("No tables extracted from %s", file_path.name)
            
            
        return pd.concat(file_frames, ignore_index=True) if file_frames else pd.DataFrame()

    def _table_to_df(self, ws, table_ref) -> Optional[pd.DataFrame]:
        """Converts an openpyxl table range to a Pandas DataFrame."""
        try:
            data = list(ws[table_ref])
            if not data:
                return None

            # Header handling: handle empty cells in header row
            headers = []
            for i, cell in enumerate(data[0]):
                val = cell.value
                headers.append(str(val).strip() if val else f"col_{i}")

            # Data handling
            values = [[cell.value for cell in row] for row in data[1:]]

            # Create DF
            df = pd.DataFrame(values, columns=headers)

            # Clean up: remove fully empty rows/cols if any
            df.dropna(how='all', inplace=True)
            df.dropna(axis=1, how='all', inplace=True)

            if df.empty:
                logger.warning("Table at %s has no data rows", table_ref)
                return None

            return df

        except Exception as e:
            logger.error("Error converting table at %s: %s",
                         table_ref, e, exc_info=True)
            return None
                     
    def extract_from_directory(
        self,
        directory: Path,
        table_prefix: str,
        exclude_sheets: List[str] = None,
        file_pattern: str = "*.xlsx"
    ) -> pd.DataFrame:
        """
        Scans a directory for Excel files matching pattern and extracts tables 
        starting with a specific prefix.
        
        Args:
            directory: Path to directory containing Excel files
            table_prefix: Prefix for table names to extract (e.g., "Sales", "Target")
            exclude_sheets: List of sheet names to skip
            file_pattern: Glob pattern for files (default: "*.xlsx")
        """
        frames: List[pd.DataFrame] = []
        exclude_sheets = [s.lower() for s in (exclude_sheets or [])]

        if not directory.exists():
            logger.warning("Directory %s does not exist.", directory)
            return pd.DataFrame()

        # Use the file pattern (e.g., "ExSD-Sales-*.xlsx")
        matching_files = sorted(directory.glob(file_pattern))

        if not matching_files:
            logger.warning("No files matching pattern '%s' in %s",
                           file_pattern, directory)
            return pd.DataFrame()

        for file_path in matching_files:
            # Skip temporary Excel files (starting with ~$)
            if file_path.name.startswith('~$'):
                continue

            logger.info("Processing %s file: %s", table_prefix, file_path.name)
            try:
                df_file = self.extract_single_file(
                    file_path, table_prefix, exclude_sheets)
                if not df_file.empty:
                    frames.append(df_file)
            except Exception as e:
                logger.error("Failed to process %s: %s",
                             file_path.name, e, exc_info=True)
                # We continue to the next file rather than crashing the whole batch
                continue

        if not frames:
            logger.warning("No data extracted from any files in %s", directory)
            return pd.DataFrame()

        return pd.concat(frames, ignore_index=True)
