"""
Reference extractor — References.xlsx.

1. Schema comes from ingestion/contracts/contracts.yaml via
   load_contracts(), not from inline `expected` sets per method — one
   declared schema, not two. 
2. Column normalization strips internal spaces too, matching the same
   fix applied to kp_sd_extractor.py.
3. Added a duplicate (sd_id, effective_from) check in
   _extract_clients_sd — two rows for the same SD with an identical
   effective_from give stg_clientsd_data.sql's LEAD()-based valid_to
   computation an ambiguous ordering to resolve. Flagging it here catches
   the mistake at the point closest to the actual data entry.
"""
import logging
from pathlib import Path
from typing import Dict
import pandas as pd
from ingestion.contracts.loader import load_contracts
from .base_extractor import (
    PROVENANCE_SOURCE_COLUMNS,
    BaseExcelExtractor,
    stable_row_id,
)


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
                     City, Is_destocked, effective_from
    - Ref_sku_mapping: kp_sku, internal_sku
    """

    def __init__(self, batch_id: str) -> None:
        super().__init__(batch_id)
        self._contracts = load_contracts()

    def read(self, directory_path: Path) -> Dict[str, pd.DataFrame]:
        """
        Extract all reference tables.
        Returns dict with keys: 'ref_salesteam', 'ref_products',
        'ref_clients_sd', 'ref_kp_sku_mapping'
        """
        reference_file = directory_path / "References.xlsx"

        if not reference_file.exists():
            logger.error("References.xlsx not found in %s", directory_path)
            return {}

        logger.info("Processing reference file: %s", reference_file)

        results = {}

        df_salesteam = self._extract_salesteam(reference_file)
        if not df_salesteam.empty:
            results['ref_salesteam'] = df_salesteam

        df_products = self._extract_product(reference_file)
        if not df_products.empty:
            results['ref_products'] = df_products

        df_clients = self._extract_clients_sd(reference_file)
        if not df_clients.empty:
            results['ref_clients_sd'] = df_clients

        df_kp_sku = self._extract_kp_sku_mapping(reference_file)
        if not df_kp_sku.empty:
            results['ref_kp_sku_mapping'] = df_kp_sku

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
        
        is_valid, missing, unexpected = self._contracts["ref_salesteam"].validate(
            set(df.columns) - set(PROVENANCE_SOURCE_COLUMNS)
        )
        if not is_valid:
            logger.error("Salesteam reference missing columns: %s", missing)
            return pd.DataFrame()
        if unexpected:
            logger.warning("Unexpected columns in salesteam reference: %s", unexpected)

        df['salesteam_ref_id'] = stable_row_id(
            df, key_columns=['salesperson_id'], prefix='ref_salesteam')

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

        is_valid, missing, unexpected = self._contracts["ref_products"].validate(
            set(df.columns) - set(PROVENANCE_SOURCE_COLUMNS)
        )
        if not is_valid:
            logger.error("Product reference missing columns: %s", missing)
            return pd.DataFrame()
        if unexpected:
            logger.warning("Unexpected columns in product reference: %s", unexpected)

        df['product_ref_id'] = stable_row_id(
            df, key_columns=['sku'], prefix='ref_products')

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

        is_valid, missing, unexpected = self._contracts["ref_clients_sd"].validate(
            set(df.columns) - set(PROVENANCE_SOURCE_COLUMNS)
        )
        if not is_valid:
            logger.error("Clients SD reference missing columns: %s", missing)
            return pd.DataFrame()
        if unexpected:
            logger.warning("Unexpected columns in clients/SD reference: %s", unexpected)

        # Duplicate (sd_id, effective_from) check — see module docstring #3.
        # An ambiguous LEAD() ordering downstream is a silent SCD2 bug;
        # catch it here, closest to the actual data-entry mistake.
        dup_effective = df[df.duplicated(['sd_id', 'effective_from'], keep=False)]
        if not dup_effective.empty:
            logger.warning(
                "Found %d rows with duplicate (sd_id, effective_from) pairs "
                "in clients/SD reference — this will produce an ambiguous "
                "valid_to ordering in stg_clientsd_data.sql's SCD2 logic.",
                len(dup_effective)
            )

        df['client_sd_ref_id'] = stable_row_id(
            df, key_columns=['sd_id'], prefix='ref_clients_sd')

        logger.info("Extracted %d client/SD reference rows", len(df))

        return df

    def _extract_kp_sku_mapping(self, file_path: Path) -> pd.DataFrame:
        """Extract Ref_sku_mapping table from References.xlsx."""
        df = self.extract_single_file(
            file_path,
            table_prefix="Ref_sku_mapping",
            exclude_sheets=[]
        )

        if df.empty:
            logger.warning("No KP-SKU mapping reference data found")
            return pd.DataFrame()

        contract = self._contracts["ref_kp_sku_mapping"]
        is_valid, missing, unexpected = contract.validate(
            set(df.columns) - set(PROVENANCE_SOURCE_COLUMNS)
        )
        if not is_valid:
            logger.error("KP-SKU mapping reference missing columns: %s", missing)
            return pd.DataFrame()
        if unexpected:
            logger.warning("Unexpected columns in KP-SKU mapping reference: %s", unexpected)

        duplicate_kp_skus = df[df.duplicated('kp_sku', keep=False)]
        if not duplicate_kp_skus.empty:
            logger.warning(
                "Found %d duplicate kp_sku values in mapping reference",
                len(duplicate_kp_skus)
            )

        df['kp_sku_mapping_ref_id'] = stable_row_id(
            df, key_columns=['kp_sku'], prefix='ref_kp_sku_mapping')

        logger.info("Extracted %d KP-SKU mapping rows", len(df))

        return df
