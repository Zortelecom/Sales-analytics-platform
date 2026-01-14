import uuid
import logging
from pathlib import Path
import pandas as pd
from .base_extractor import BaseExcelExtractor

logger = logging.getLogger(__name__)


class TargetExtractor(BaseExcelExtractor):
    """
    Extract Target data from Sales_Targets.xlsx file.
    Table name: Targets or Targets_table
    Structure: month_year, salesperson_id, product_cat, target_amount
    """

    REQUIRED_COLUMNS = {'month_year', 'salesperson_id',
                        'product_category', 'target_amount'}

    def read(self, directory_path: Path) -> pd.DataFrame:
        """Extract targets with product category dimension."""

        # Look specifically for Sales_Targets.xlsx
        target_file = directory_path / "Sales_Targets.xlsx"

        if not target_file.exists():
            logger.warning(
                "Sales_Targets.xlsx not found in %s", directory_path)
            return pd.DataFrame()

        df = self.extract_single_file(
            target_file,
            table_prefix="Target",  # Matches "Targets" or "Targets_table"
            exclude_sheets=[]
        )

        if df.empty:
            logger.warning("No targets extracted from %s", target_file)
            return df

        # Normalize column names
        df.columns = [col.lower().strip() for col in df.columns]

        # Validation
        missing = self.REQUIRED_COLUMNS - set(df.columns)
        if missing:
            logger.error("Target data missing required columns: %s", missing)
            return pd.DataFrame()

        # Add primary key
        df["target_line_id"] = [str(uuid.uuid4()) for _ in range(len(df))]

        # Data quality: Check for nulls in critical fields
        null_counts = df[list(self.REQUIRED_COLUMNS)].isnull().sum()
        if null_counts.any():
            logger.warning("Null values found in targets:\n%s",
                           null_counts[null_counts > 0])

        logger.info("Extracted %d target rows for %d salespersons across %d product categories",
                    len(df),
                    df['salesperson_id'].nunique(),
                    df['product_category'].nunique())

        return df
