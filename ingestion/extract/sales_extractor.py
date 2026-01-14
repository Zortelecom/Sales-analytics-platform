from __future__ import annotations
import uuid
import logging
import re
from pathlib import Path
import pandas as pd
from .base_extractor import BaseExcelExtractor

logger = logging.getLogger(__name__)


class SalesExtractor(BaseExcelExtractor):
    """
    Extract Sales data from ExSD-Sales-{SubRegion}.xlsx files.
    Only processes sheets named after salesperson full names.
    Tables must be named SalesXXX.
    """

    # Define expected columns (case-insensitive matching)
    REQUIRED_COLUMNS = {
        'salesperson_id', 'clientsd_id', 'date', 'sku', 'qty', 'amount'
    }

    EXPECTED_COLUMNS = {
        'salesperson_id', 'clientsd_id', 'date', 'sku', 'product_name', 'unit_price', 'qty', 'amount',
        'subregion', 'salesperson', 'supervisor', 'channel', 'city',
        'product_cat', 'product_subcat', 'unit_weight', 'is_innovation'
    }

    def read(self, directory_path: Path) -> pd.DataFrame:
        """Extract sales data with validation."""
        df = self.extract_from_directory(
            directory_path,
            table_prefix="Sales",
            file_pattern="ExSD-Sales-*.xlsx"
        )

        if df.empty:
            logger.warning("No sales data extracted")
            return pd.DataFrame()

        # Normalize column names (Excel might have different casing)
        df.columns = [col.lower().strip() for col in df.columns]

        # Validate required columns
        missing = self.REQUIRED_COLUMNS - set(df.columns)
        if missing:
            logger.error("Missing required columns: %s", missing)
            return pd.DataFrame()

        # Warn about unexpected columns
        unexpected = set(df.columns) - self.EXPECTED_COLUMNS - {
            'source_file', 'sheet_name', 'table_name',
            'ingestion_ts', 'ingestion_batch_id'
        }
        if unexpected:
            logger.warning("Unexpected columns found: %s", unexpected)

        # Add primary key
        df["sales_line_id"] = [str(uuid.uuid4()) for _ in range(len(df))]

        # Extract subregion from filename
        df['filename_subregion'] = df['source_file'].apply(
            self._extract_subregion_from_filename
        )

        logger.info("Extracted %d sales rows from %d files",
                    len(df), df['source_file'].nunique())

        return df

    def _extract_subregion_from_filename(self, filename: str) -> str:
        """
        Extract subregion from filename like 'ExSD-Sales-YaoundeNord.xlsx'
        Returns: 'YaoundeNord' or 'UNKNOWN'
        """
        pattern = r'ExSD-Sales-(.+)\.xlsx'
        match = re.search(pattern, filename)
        return match.group(1) if match else 'UNKNOWN'

    def validate_sheet_name(self, sheet_name: str, workbook_path: Path) -> bool:
        """
        Check if sheet should be processed.
        Override this in base_extractor to allow custom filtering.

        For sales: Only process sheets named after salesperson full names.
        Exclude: 'synthese', 'summary', 'template', etc.
        """
        sheet_lower = sheet_name.lower()

        # Exclude common non-data sheets
        exclude_keywords = ['synthese', 'summary',
                            'template', 'config', 'readme']

        if any(keyword in sheet_lower for keyword in exclude_keywords):
            logger.debug(
                "Skipping sheet '%s' - matches exclude pattern", sheet_name)
            return False

        # Sales sheets should be person names (heuristic: contains space or is >3 chars)
        if len(sheet_name) > 3 and not sheet_name.startswith('_'):
            return True

        logger.debug(
            "Skipping sheet '%s' - doesn't match person name pattern", sheet_name)
        return False
