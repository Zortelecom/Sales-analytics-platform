"""
KP-SD extractors — ExKP-*.xlsx files.

Two sources:
- KPDestockeExtractor:    ExKP-Destocke-*.xlsx (directory glob, one sheet
                          per SD, table prefix SalesKP)
- KPNonDestockeExtractor: ExKP-NonDestocke.xlsx (single file, single
                          table SalesKP_NonDestocke)

"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

from .base_extractor import BaseExcelExtractor
from ..contracts.loader import load_contracts

logger = logging.getLogger(__name__)


class KPDestockeExtractor(BaseExcelExtractor):
    """
    Extract KP destocké sales data from ExKP-Destocke_*.xlsx files.
    One sheet per SD; tables named SalesKP_<SDName>.
    """

    CONTRACT_NAME = "kp_sd_destocke"

    def __init__(self, batch_id: str) -> None:
        super().__init__(batch_id)
        self._contract = load_contracts()[self.CONTRACT_NAME]

    def read(self, directory_path: Path) -> pd.DataFrame:
        """Extract KP destocké data with validation and null-key flagging."""
        df = self.extract_from_directory(
            directory_path,
            table_prefix="SalesIn",
            file_pattern="ExKP-Destocke_*.xlsx",
            required_columns=self._contract.required,
        )

        if df.empty:
            logger.warning("No KP destocké data extracted")
            return pd.DataFrame()

        df.columns = [col.lower().strip() for col in df.columns]

        is_valid, missing, unexpected = self._contract.validate(set(df.columns))
        if not is_valid:
            logger.error("Missing required columns: %s", missing)
            return pd.DataFrame()
        if unexpected:
            logger.warning("Unexpected columns found: %s", unexpected)

        df.replace(
            ["None", "NaT", "nan", "-", "#N/A"],
            np.nan,
            inplace=True,
        )

        # Null-key flagging — sale_date, sku, AND sd_id (see fix #5 above)
        null_mask = df["sale_date"].isna() | df["sku"].isna() | df["sd_id"].isna()
        df["has_null_key"] = null_mask

        null_count = null_mask.sum()
        if null_count > 0:
            per_file = (
                df.loc[null_mask, "source_file"]
                .value_counts()
                .to_dict()
            )
            for src_file, count in per_file.items():
                logger.warning(
                    "%d row(s) with null sale_date, sku, or sd_id in %s — "
                    "flagged as has_null_key=True, kept in raw seed.",
                    count, src_file,
                )

        df["kp_sd_line_id"] = [str(uuid.uuid4()) for _ in range(len(df))]

        logger.info(
            "Extracted %d KP destocké rows from %d file(s) "
            "(%d with null keys).",
            len(df),
            df["source_file"].nunique(),
            null_count,
        )

        return df


class KPNonDestockeExtractor(BaseExcelExtractor):
    """
    Extract KP non-destocké sales data from ExKP-NonDestocke.xlsx.
    Single file, single table (SalesKP_NonDestocke) inside the given
    directory — sd_id is an ordinary column here, not encoded per-sheet.
    """

    CONTRACT_NAME = "kp_sd_non_destocke"

    def __init__(self, batch_id: str) -> None:
        super().__init__(batch_id)
        self._contract = load_contracts()[self.CONTRACT_NAME]

    def read(self, directory_path: Path) -> pd.DataFrame:
        """Extract KP non-destocké data from single file in directory."""
        file_path = directory_path / "ExKP-NonDestocke.xlsx"

        if not file_path.exists():
            logger.warning("ExKP-NonDestocke.xlsx not found in %s", directory_path)
            return pd.DataFrame()

        df = self.extract_single_file(
            file_path,
            table_prefix="SalesIn_NonDestocke",
            exclude_sheets=[],
            required_columns=self._contract.required,
        )

        if df.empty:
            logger.warning("No KP non-destocké data extracted")
            return pd.DataFrame()

        df.columns = [col.lower().strip() for col in df.columns]

        is_valid, missing, unexpected = self._contract.validate(set(df.columns))
        if not is_valid:
            logger.error("Missing required columns: %s", missing)
            return pd.DataFrame()
        if unexpected:
            logger.warning("Unexpected columns found: %s", unexpected)

        df.replace(
            ["None", "NaT", "nan", "-", "#N/A"],
            np.nan,
            inplace=True,
        )

        null_mask = df["sale_date"].isna() | df["sku"].isna() | df["sd_id"].isna()
        df["has_null_key"] = null_mask

        null_count = null_mask.sum()
        if null_count > 0:
            per_file = (
                df.loc[null_mask, "source_file"]
                .value_counts()
                .to_dict()
            )
            for src_file, count in per_file.items():
                logger.warning(
                    "%d row(s) with null sale_date, sku, or sd_id in %s — "
                    "flagged as has_null_key=True, kept in raw seed.",
                    count, src_file,
                )

        df["kp_sd_line_id"] = [str(uuid.uuid4()) for _ in range(len(df))]

        logger.info(
            "Extracted %d KP non-destocké rows (%d with null keys).",
            len(df),
            null_count,
        )

        return df
