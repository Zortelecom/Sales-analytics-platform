"""
Target extractor — Sales_Targets.xlsx.

"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pandas as pd

from .base_extractor import BaseExcelExtractor

logger = logging.getLogger(__name__)


class TargetExtractor(BaseExcelExtractor):
    """
    Extract Target data from Sales_Targets.xlsx.

    Expected table name: Targets or Targets_table.
    Expected columns:    month_year, salesperson_id, product_category,
                         target_amount.
    """

    REQUIRED_COLUMNS: set = {
        "month_year",
        "salesperson_id",
        "product_category",
        "target_amount",
    }

    def read(self, directory_path: Path) -> pd.DataFrame:
        """Extract targets with product-category dimension."""
        target_file = directory_path / "Sales_Targets.xlsx"

        if not target_file.exists():
            logger.warning("Sales_Targets.xlsx not found in %s", directory_path)
            return pd.DataFrame()

        df = self.extract_single_file(
            target_file,
            table_prefix="Target",   # matches "Targets" or "Targets_table"
            exclude_sheets=[],
        )

        if df.empty:
            logger.warning("No targets extracted from %s", target_file)
            return df

        # Normalise column names
        df.columns = [col.lower().strip() for col in df.columns]

        # Validate required columns
        missing = self.REQUIRED_COLUMNS - set(df.columns)
        if missing:
            logger.error("Target data missing required columns: %s", missing)
            return pd.DataFrame()

        # Surrogate primary key
        df["target_line_id"] = [str(uuid.uuid4()) for _ in range(len(df))]

        # Log null counts in critical fields (informational — not a failure)
        null_counts = df[list(self.REQUIRED_COLUMNS)].isnull().sum()
        if null_counts.any():
            logger.warning(
                "Null values found in targets:\n%s",
                null_counts[null_counts > 0],
            )

        logger.info(
            "Extracted %d target rows for %d salesperson(s) "
            "across %d product category(ies).",
            len(df),
            df["salesperson_id"].nunique(),
            df["product_category"].nunique(),
        )

        return df