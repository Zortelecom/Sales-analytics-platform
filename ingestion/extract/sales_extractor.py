"""
Sales extractor — ExSD-Sales-{SubRegion}.xlsx files.

Fix applied
───────────
Previously, rows with a null sale_date or sku were silently dropped inside
the extractor via dropna().  They never appeared in any audit, quality
report, or log that survives beyond the pipeline run.

The raw layer should always contain every row the source file contains.
Filtering is staging's job.  The fix:

  1. A boolean column `has_null_key` is added to the raw seed.
     True  → sale_date or sku is null (row needs attention).
     False → both keys are present (normal row).

  2. The count of null-key rows is logged per source file so operators
     get an immediate attribution without opening the source Excel.

  3. The actual WHERE-clause filter (DROP rows with null keys) belongs in
     stg_sales.sql — not here.  Add:
         WHERE sale_date IS NOT NULL AND sku IS NOT NULL
     to the staging model and an assert_no_null_keys audit on raw_sales
     that logs but does not fail (historical count of dirty source rows).
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

from .base_extractor import BaseExcelExtractor

logger = logging.getLogger(__name__)


class SalesExtractor(BaseExcelExtractor):
    """
    Extract Sales data from ExSD-Sales-{SubRegion}.xlsx files.
    Only processes sheets that look like salesperson full names.
    Tables must be named SalesXXX.
    """

    REQUIRED_COLUMNS: set = {
        "salesperson_id", "sd_id", "sale_date", "sku", "qty", "amount",
    }

    EXPECTED_COLUMNS: set = {
        "salesperson_id", "sd_id", "sale_date", "sku", "product_name",
        "unit_price", "qty", "amount", "subregion", "salesperson",
        "supervisor", "channel", "city", "product_cat", "product_subcat",
        "unit_weight", "is_innovation",
    }

    # ── Public API ───────────────────────────────────────────────────────────

    def read(self, directory_path: Path) -> pd.DataFrame:
        """Extract sales data with validation and null-key flagging."""
        df = self.extract_from_directory(
            directory_path,
            table_prefix="Sales",
            file_pattern="ExSD-Sales-*.xlsx",
            required_columns=self.REQUIRED_COLUMNS,
        )

        if df.empty:
            logger.warning("No sales data extracted")
            return pd.DataFrame()

        # Normalise column names
        df.columns = [col.lower().strip() for col in df.columns]

        # Hard-fail if required columns are missing after normalisation
        missing = self.REQUIRED_COLUMNS - set(df.columns)
        if missing:
            logger.error("Missing required columns: %s", missing)
            return pd.DataFrame()

        # Warn about unexpected columns (schema drift from source files)
        _provenance = {"source_file", "sheet_name", "table_name",
                       "ingestion_ts", "ingestion_batch_id"}
        unexpected = set(df.columns) - self.EXPECTED_COLUMNS - _provenance
        if unexpected:
            logger.warning("Unexpected columns found: %s", unexpected)

        # Normalise string representations of nulls to real NaN
        df.replace(
            ["None", "NaT", "nan", "-", "#N/A"],
            np.nan,
            inplace=True,
        )

        # ── Null-key audit (replaces the old silent dropna) ─────────────────
        # We keep all rows in the raw seed so the full source record is
        # preserved for auditing.  Staging is responsible for filtering.
        null_mask = df["sale_date"].isna() | df["sku"].isna()
        df["has_null_key"] = null_mask

        null_count = null_mask.sum()
        if null_count > 0:
            # Log per source file so the operator knows exactly which Excel
            # file produced dirty rows without having to open it.
            per_file = (
                df.loc[null_mask, "source_file"]
                .value_counts()
                .to_dict()
            )
            for src_file, count in per_file.items():
                logger.warning(
                    "%d row(s) with null sale_date or sku in %s — "
                    "flagged as has_null_key=True, kept in raw seed.",
                    count, src_file,
                )
        else:
            logger.debug("All rows have non-null sale_date and sku.")

        # Unique surrogate key per sales line
        df["sales_line_id"] = [str(uuid.uuid4()) for _ in range(len(df))]

        # Subregion derived from filename (used for source tracing in audits)
        df["filename_subregion"] = df["source_file"].apply(
            self._extract_subregion_from_filename
        )

        logger.info(
            "Extracted %d sales rows from %d file(s) "
            "(%d with null keys — see has_null_key column).",
            len(df),
            df["source_file"].nunique(),
            null_count,
        )

        return df

    # ── Sheet-level hook (override of BaseExcelExtractor.validate_sheet_name) ──

    def validate_sheet_name(self, sheet_name: str, file_path: Path) -> bool:
        """
        Accept only sheets that look like salesperson full names.

        Excludes: synthese, summary, template, config, readme, and any
        sheet whose name starts with an underscore or is very short (≤ 3
        characters — likely a code or placeholder).
        """
        sheet_lower = sheet_name.lower()

        exclude_keywords = ["synthese", "summary", "template", "config", "readme"]
        if any(kw in sheet_lower for kw in exclude_keywords):
            logger.debug("Skipping sheet '%s' — matches exclude pattern", sheet_name)
            return False

        if len(sheet_name) > 3 and not sheet_name.startswith("_"):
            return True

        logger.debug(
            "Skipping sheet '%s' — doesn't match person name pattern", sheet_name
        )
        return False

    # ── Private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_subregion_from_filename(filename: str) -> str:
        """
        Extract the subregion slug from a filename like
        'ExSD-Sales-YaoundeNord.xlsx'.

        Returns the slug (e.g. 'YaoundeNord') or 'UNKNOWN' if the pattern
        doesn't match.
        """
        match = re.search(r"ExSD-Sales-(.+)\.xlsx", filename)
        return match.group(1) if match else "UNKNOWN"