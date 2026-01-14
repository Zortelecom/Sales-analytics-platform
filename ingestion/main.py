"""
Main entry point for Sales Analytics Ingestion Pipeline
SEED-BASED APPROACH: Extract Excel → Write CSV seeds for SQLMesh
"""
import logging
import sys
import shutil
from datetime import datetime, timezone
from typing import Dict
from pathlib import Path
import pandas as pd

# Imports from our modules
from .config import settings
from .extract.sales_extractor import SalesExtractor
from .extract.target_extractor import TargetExtractor
from .extract.reference_extractor import ReferenceExtractor
from .load.seed_writer import SeedWriter

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("IngestionOrchestrator")


def generate_batch_id() -> str:
    """Generate a unique batch ID with timestamp."""
    return f"batch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


def archive_processed_files(batch_id: str) -> int:
    """
    Move processed Excel files to archive directory.
    
    Returns:
        Number of files archived
    """
    archive_path = settings.ARCHIVE_DIR / batch_id
    archive_path.mkdir(parents=True, exist_ok=True)

    archived_count = 0
    for source_dir in [settings.INPUT_SALES_DIR, settings.INPUT_TARGETS_DIR]:
        for file in source_dir.glob("*.xlsx"):
            if file.name.startswith('~$'):  # Skip temp files
                continue
            dest = archive_path / file.name
            shutil.move(str(file), str(dest))
            logger.info("Archived: %s → %s", {file.name}, {dest})
            archived_count += 1

    return archived_count


def run_pipeline():
    """Execute the sales analytics ingestion pipeline - CSV seed version."""
    batch_id = generate_batch_id()

    logger.info("="*70)
    logger.info("SALES ANALYTICS INGESTION PIPELINE - SEED-BASED")
    logger.info("="*70)
    logger.info("Batch ID: %s", batch_id)
    logger.info("Timestamp: %s", datetime.now(timezone.utc).isoformat())
    logger.info("="*70)

    # Ensure directories exist
    settings.INPUT_SALES_DIR.mkdir(parents=True, exist_ok=True)
    settings.INPUT_TARGETS_DIR.mkdir(parents=True, exist_ok=True)
    settings.INPUT_REFERENCES_DIR.mkdir(parents=True, exist_ok=True)
    settings.SQLMESH_SEEDS_DIR.mkdir(parents=True, exist_ok=True)
    
    tables: Dict[str, pd.DataFrame] = {}
    extracted_files = []

    try:
        # ==========================================
        # PHASE 1: EXTRACTION
        # ==========================================
        logger.info("\n[PHASE 1/3] EXTRACTION")
        logger.info("-" * 50)

        # Extract Sales
        logger.info("Extracting sales data...")
        sales_extractor = SalesExtractor(batch_id)
        df_sales = sales_extractor.read(settings.INPUT_SALES_DIR)

        if not df_sales.empty:
            tables["raw_sales"] = df_sales
            logger.info("✅ Extracted %d sales rows from %d files",
                        len(df_sales), df_sales['source_file'].nunique())
            extracted_files.extend(
                settings.INPUT_SALES_DIR.glob("ExSD-Sales-*.xlsx")
            )
        else:
            logger.warning("⚠️  No sales data found")

        # Extract Targets
        logger.info("Extracting targets data...")
        target_extractor = TargetExtractor(batch_id)
        df_targets = target_extractor.read(settings.INPUT_TARGETS_DIR)

        if not df_targets.empty:
            tables["raw_targets"] = df_targets
            logger.info("✅ Extracted %d target rows", len(df_targets))
            target_file = settings.INPUT_TARGETS_DIR / "Sales_Targets.xlsx"
            if target_file.exists():
                extracted_files.append(target_file)
        else:
            logger.warning("⚠️  No targets data found")
            
        # 2c. Extract References (References.xlsx)
        logger.info("\nExtracting REFERENCE data...")
        reference_extractor = ReferenceExtractor(batch_id)
        tables_ref = reference_extractor.read(settings.INPUT_REFERENCES_DIR)
        df_products = tables_ref.get("ref_products", pd.DataFrame())
        df_clients_sd = tables_ref.get("ref_clients_sd", pd.DataFrame())
        df_salesteam = tables_ref.get("ref_salesteam", pd.DataFrame())
        

        if not df_products.empty:
            tables["raw_ref_products"] = df_products
            logger.info("✅ Extracted product reference data: %d rows", len(df_products))
        else:
            logger.warning("⚠️  No product reference data found")
        
        if not df_clients_sd.empty:
            tables["raw_ref_clients_sd"] = df_clients_sd
            logger.info("✅ Extracted clients SD reference data: %d rows", len(df_clients_sd))
        else:
            logger.warning("⚠️  No clients SD reference data found")
        
        if not df_salesteam.empty:
            tables["raw_ref_salesteam"] = df_salesteam
            logger.info("✅ Extracted reference data: %d rows",
                        len(df_salesteam))
        else:
            logger.warning("⚠️  No salesteam data found")

        if not tables_ref:
            logger.warning("⚠️  No reference data found in References.xlsx.")
            return


        # Check if any data was extracted
        if all(df.empty for df in [df_sales, df_targets, df_products, df_clients_sd, df_salesteam]):
            logger.warning("No data found in input directories. Exiting.")
            return
        
         # Summary
        logger.info("\n" + "=" * 80)
        logger.info("📊 EXTRACTION SUMMARY")
        logger.info("-" * 80)
        for table_name, df in tables.items():
            logger.info("  %s: %d rows", table_name, len(df))
        logger.info("=" * 80)


        # ==========================================
        # PHASE 2: WRITE CSV SEEDS
        # ==========================================
        logger.info("\n[PHASE 2/3] WRITING CSV SEEDS")
        logger.info("-" * 50)

        # Initialize seed writer
        seed_writer = SeedWriter(
            seeds_dir=settings.SQLMESH_SEEDS_DIR,
            batch_id=batch_id
        )

        # Prepare seeds configuration
        seeds_to_write = {}
        if not df_sales.empty:
            seeds_to_write['sales_data'] = df_sales
        if not df_targets.empty:
            seeds_to_write['targets_data'] = df_targets
        if not df_products.empty:
            seeds_to_write['products_data'] = df_products
        if not df_clients_sd.empty:
            seeds_to_write['clientSD_data'] = df_clients_sd
        if not df_salesteam.empty:
            seeds_to_write['salesteam_data'] = df_salesteam 

        # Write all seeds
        written_paths = seed_writer.write_seeds(seeds_to_write)

        # Log results
        for seed_name, path in written_paths.items():
            logger.info(
                "📄 Seed ready: %s", path.relative_to(settings.PROJECT_ROOT))

        # Optional: Clean up old metadata files
        seed_writer.cleanup_old_seeds(keep_latest=5)

        # ==========================================
        # PHASE 3: ARCHIVE SOURCE FILES
        # ==========================================
        logger.info("\n[PHASE 3/3] ARCHIVING SOURCE FILES")
        logger.info("-" * 50)

        archived = archive_processed_files(batch_id)
        logger.info(
            "✅ Archived %s Excel file(s) to %s", archived, settings.ARCHIVE_DIR / batch_id)

        # ==========================================
        # SUMMARY
        # ==========================================
        logger.info("\n" + "="*70)
        logger.info("✅ INGESTION COMPLETE")
        logger.info("="*70)

        # Statistics
        stats = {
            'Batch ID': batch_id,
            'Sales Records': f"{len(df_sales):,}" if not df_sales.empty else "0",
            'Targets Records': f"{len(df_targets):,}" if not df_targets.empty else "0",
            'Products Records': f"{len(df_products):,}" if not df_products.empty else "0",
            'ClientsSD Records': f"{len(df_clients_sd):,}" if not df_clients_sd.empty else "0",
            'Salesteam Records': f"{len(df_salesteam):,}" if not df_salesteam.empty else "0",
            'Seeds Written': len(written_paths),
            'Files Archived': archived,
            'Seeds Directory': str(settings.SQLMESH_SEEDS_DIR.relative_to(settings.PROJECT_ROOT))
        }

        for key, value in stats.items():
            logger.info("%s %s", key, value)

        # Next steps
        logger.info("\n" + "="*70)
        logger.info("NEXT STEPS:")
        logger.info("="*70)
        logger.info("1. Review CSV seeds in: sqlmesh/seeds/")
        logger.info("2. Run SQLMesh transformations:")
        logger.info("   cd sqlmesh")
        logger.info("   sqlmesh plan dev")
        logger.info("3. Launch dashboard:")
        logger.info("   streamlit run demo_dashboard.py")
        logger.info("="*70)

        # Optional: Show seed summary
        logger.info("\nSeed Summary:")
        summary = seed_writer.get_seed_summary()
        for seed_name, info in summary.items():
            logger.info("  %s:", seed_name)
            logger.info("    - Rows: %s", info['rows'])
            logger.info("    - Size: %.2f MB", info['size_mb'])
            logger.info(
                "    - Modified: %s", info['modified'].strftime('%Y-%m-%d %H:%M:%S'))

    except Exception as e:
        logger.error("\n" + "="*70)
        logger.error("❌ PIPELINE FAILED")
        logger.error("="*70)
        logger.error("Error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    run_pipeline()
