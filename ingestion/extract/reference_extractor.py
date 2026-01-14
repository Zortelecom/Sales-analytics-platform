import uuid
import pandas as pd
import logging
from pathlib import Path
from typing import Dict
from .base_extractor import BaseExcelExtractor

logger = logging.getLogger(__name__)


class ReferenceExtractor(BaseExcelExtractor):
    """
    Extract Reference data from References.xlsx.

    Tables:
    - Ref_Salesteam: Salesperson_id, FullName, Region, SubRegion, Channel, 
                     Supervisor
    - Ref_Product: SKU, Product_name, product_category, product_subcategory, 
                   unit_price, unit_weight, is_innovation
    - Ref_ClientsSD: SD_id, SD_NAME, Region, KP, SubRegion, phone, 
                     City, Is_destocked
    """

    def read(self, directory_path: Path) -> Dict[str, pd.DataFrame]:
        """
        Extract all reference tables.
        Returns dict with keys: 'salesteam', 'product', 'clients_sd'
        """
        reference_file = directory_path / "References.xlsx"

        if not reference_file.exists():
            logger.error("References.xlsx not found in %s", directory_path)
            return {}

        logger.info("Processing reference file: %s", reference_file)

        results = {}

        # Extract Salesteam reference
        df_salesteam = self._extract_salesteam(reference_file)
        if not df_salesteam.empty:
            results['ref_salesteam'] = df_salesteam

        # Extract Product reference
        df_products = self._extract_product(reference_file)
        if not df_products.empty:
            results['ref_products'] = df_products

        # Extract Clients/SD reference
        df_clients = self._extract_clients_sd(reference_file)
        if not df_clients.empty:
            results['ref_clients_sd'] = df_clients

        return results

    def _extract_salesteam(self, file_path: Path) -> pd.DataFrame:
        """Extract Ref_Salesteam table from Salespersons_List sheet."""
        df = self.extract_single_file(
            file_path,
            table_prefix="Ref_Salesteam",
            exclude_sheets=[]
        )

        if df.empty:
            logger.warning("No salesteam reference data found")
            return pd.DataFrame()

        # Normalize columns
        df.columns = [col.lower().strip() for col in df.columns]

        # Expected columns
        expected = {
            'salesperson_id', 'fullname', 'region', 'subregion',
            'channel', 'supervisor'
        }

        missing = expected - set(df.columns)
        if missing:
            logger.error("Salesteam reference missing columns: %s", missing)
            return pd.DataFrame()

        # Add record ID
        df['salesteam_ref_id'] = [str(uuid.uuid4()) for _ in range(len(df))]

        logger.info("Extracted %d salesteam reference rows (%d unique salespersons)",
                    len(df), df['salesperson_id'].nunique())

        return df

    def _extract_product(self, file_path: Path) -> pd.DataFrame:
        """Extract Ref_Product table from Product_List sheet."""
        df = self.extract_single_file(
            file_path,
            table_prefix="Ref_Products",
            exclude_sheets=[]
        )

        if df.empty:
            logger.warning("No product reference data found")
            return pd.DataFrame()

        # Normalize columns
        df.columns = [col.lower().strip() for col in df.columns]

        # Expected columns
        expected = {
            'sku', 'product_name', 'product_category',
            'product_subcategory', 'unit_price', 'unit_weight',
            'is_innovation'
        }

        missing = expected - set(df.columns)
        if missing:
            logger.error("Product reference missing columns: %s", missing)
            return pd.DataFrame()

        # Add record ID
        df['product_ref_id'] = [str(uuid.uuid4()) for _ in range(len(df))]

        # Data quality checks
        duplicate_skus = df[df.duplicated('sku', keep=False)]
        if not duplicate_skus.empty:
            logger.warning("Found %d duplicate SKUs in product reference",
                           len(duplicate_skus))

        logger.info("Extracted %d product reference rows", len(df))

        return df

    def _extract_clients_sd(self, file_path: Path) -> pd.DataFrame:
        """Extract Ref_ClientsSD table from SD_List sheet."""
        df = self.extract_single_file(
            file_path,
            table_prefix="Ref_ClientsSD",
            exclude_sheets=[]
        )

        if df.empty:
            logger.warning("No clients/SD reference data found")
            return pd.DataFrame()

        # Normalize columns
        df.columns = [col.lower().strip() for col in df.columns]

        # Expected columns
        expected = {
            'sd_id', 'sd_name', 'region', 'kp', 'subregion',
            'phone', 'city', 'is_destocked'
        }

        missing = expected - set(df.columns)
        if missing:
            logger.error("Clients SD reference missing columns: %s", missing)
            return pd.DataFrame()

        # Add record ID
        df['client_sd_ref_id'] = [str(uuid.uuid4()) for _ in range(len(df))]

        logger.info("Extracted %d client/SD reference rows", len(df))

        return df
