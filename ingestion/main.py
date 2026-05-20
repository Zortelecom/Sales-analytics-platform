#!/usr/bin/env python3
"""
Main entry point for Sales Analytics Ingestion Pipeline
SEED-BASED APPROACH: Extract Excel → Write CSV seeds for SQLMesh
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

# Use absolute imports for package structure
from ingestion.config.settings import ARCHIVE_DIR, load_sources_config, INPUT_PATHS, SQLMESH_SEEDS_DIR
from ingestion.orchestrate import FileDiscovery, ExcelPreprocessor, ArchiveManager
from ingestion.extract.sales_extractor import SalesExtractor
from ingestion.extract.target_extractor import TargetExtractor
from ingestion.extract.reference_extractor import ReferenceExtractor
from ingestion.load.seed_writer import SeedWriter

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_ingestion_pipeline(dry_run: bool = False,
                           skip_archive: bool = False,
                           since: datetime = None) -> bool:
    """
    Complete ingestion pipeline with orchestration
    
    Phase 1: Discover and preprocess files from SharePoint/local
    Phase 2: Extract data to seeds
    Phase 3: Archive processed files
    """
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger.info("🚀 Starting ingestion pipeline [Batch: %s]", batch_id)

    # ─────────────────────────────────────────────────────────────
    # PHASE 1: File Discovery & Preprocessing
    # ─────────────────────────────────────────────────────────────
    logger.info("\n📁 Phase 1: File Discovery")

    config = load_sources_config()
    discovery = FileDiscovery(config)
    all_files = discovery.discover_all()

    # Filter for new files only
    new_files = discovery.check_for_new_files(since=since)
    total_new = sum(len(v) for v in new_files.values())

    if total_new == 0:
        logger.info("No new files to process")
        return True

    logger.info("\nFound %d new file(s) to process", total_new)

    # Generate processing manifest
    manifest = discovery.get_processing_manifest()
    # Filter manifest to only new files
    new_file_paths = set()
    for type_files in new_files.values():
        new_file_paths.update([f.resolve() for f in type_files])
    manifest = [m for m in manifest if m["source_path"].resolve()
                in new_file_paths]

    # Preprocess Excel files
    logger.info("\n🔧 Phase 2: Preprocessing Excel Files")
    preprocessor = ExcelPreprocessor(dry_run=dry_run)
    results = preprocessor.batch_preprocess(manifest)

    if results["failed"]:
        logger.error("Failed to process %d file(s)", len(results["failed"]))
        for item in results["failed"]:
            logger.error("  - %s", item['source_path'])
        return False

    logger.info(
        "Successfully preprocessed %d file(s)", len(results['successful']))
    logger.info("Skipped %d file(s) (already current)", len(results['skipped']))

    # ─────────────────────────────────────────────────────────────
    # PHASE 2: Data Extraction (FIXED)
    # ─────────────────────────────────────────────────────────────
    logger.info("\n📊 Phase 3: Data Extraction to Seeds")

    if dry_run:
        logger.info("[DRY RUN] Would extract data to seeds")
        return True

    # Ensure input directories exist (preprocessor already created them)
    for path in INPUT_PATHS.values():
        path.mkdir(parents=True, exist_ok=True)

    # ─────────────────────────────────────────────────────────────
    # FIX: Initialize extractors with batch_id, not paths
    # ─────────────────────────────────────────────────────────────

    # Extract data dictionary to collect all results
    extracted_data = {}

    # ─────────────────────────────────────────────────────────────
    # Sales Extraction
    # ─────────────────────────────────────────────────────────────
    try:
        # FIX: SalesExtractor takes batch_id in constructor
        sales_extractor = SalesExtractor(batch_id=batch_id)
        # FIX: Use .read() method with directory path
        df_sales = sales_extractor.read(INPUT_PATHS["sales"])

        if df_sales.empty:
            logger.warning("No sales data extracted")
        else:
            extracted_data["sales_data"] = df_sales
            logger.info("✓ Extracted %d sales records", len(df_sales))
    except (FileNotFoundError, ValueError, KeyError) as e:
        logger.error("Sales extraction failed: %s", e)
        return False

    # ─────────────────────────────────────────────────────────────
    # Targets Extraction
    # ─────────────────────────────────────────────────────────────
    try:
        # FIX: TargetExtractor takes batch_id in constructor
        targets_extractor = TargetExtractor(batch_id=batch_id)
        # FIX: Use .read() method with directory path
        df_targets = targets_extractor.read(INPUT_PATHS["targets"])

        if df_targets.empty:
            logger.warning("No targets data extracted")
        else:
            extracted_data["targets_data"] = df_targets
            logger.info("✓ Extracted %d target records", len(df_targets))
    except (FileNotFoundError, ValueError, KeyError) as e:
        logger.error("Targets extraction failed: %s", e)
        return False

    # ─────────────────────────────────────────────────────────────
    # References Extraction (FIXED)
    # ─────────────────────────────────────────────────────────────
    try:
        # FIX: ReferenceExtractor takes batch_id in constructor
        ref_extractor = ReferenceExtractor(batch_id=batch_id)
        # FIX: .read() returns Dict[str, DataFrame], not single DataFrame
        ref_results = ref_extractor.read(INPUT_PATHS["references"])

        # FIX: Map the returned dictionary keys to seed names
        # Based on ReferenceExtractor.read() return structure:
        # {
        #   'ref_salesteam': df_salesteam,
        #   'ref_products': df_products,
        #   'ref_clients_sd': df_clients
        # }
        ref_mapping = {
            'ref_salesteam': 'salesteam_data',
            'ref_products': 'products_data',
            'ref_clients_sd': 'clientSD_data'
        }

        for ref_key, seed_name in ref_mapping.items():
            if ref_key in ref_results and not ref_results[ref_key].empty:
                extracted_data[seed_name] = ref_results[ref_key]
                logger.info(
                    "✓ Extracted %d %s records", len(ref_results[ref_key]), ref_key)
            else:
                logger.warning("No data for %s", ref_key)

    except (FileNotFoundError, ValueError, KeyError) as e:
        logger.error("References extraction failed: %s", e)
        return False

    # ─────────────────────────────────────────────────────────────
    # Write seeds
    # ─────────────────────────────────────────────────────────────
    seeds_dir = SQLMESH_SEEDS_DIR
    seeds_dir.mkdir(exist_ok=True)

    writer = SeedWriter(seeds_dir, batch_id)

    # FIX: Use write_seeds with the dictionary
    seed_paths = writer.write_seeds(extracted_data)

    summary = writer.get_seed_summary()
    logger.info("\n📦 Seed Summary:")
    for seed, info in summary.items():
        size_human = f"{info['size_mb']:.2f} MB" if isinstance(
            info['size_mb'], (int, float)) else "N/A"
        logger.info(" %s : %s %s rows (%s)", seed, info['size_mb'], info['rows'], size_human)

    # ─────────────────────────────────────────────────────────────
    # PHASE 3: Archiving
    # ─────────────────────────────────────────────────────────────
    if not skip_archive and not dry_run:
        logger.info("\n📦 Phase 4: Archiving Source Files")
        archive_mgr = ArchiveManager(batch_id=batch_id, archive_base=ARCHIVE_DIR)
        archive_path = archive_mgr.archive_processed_files(
            manifest=results["successful"],
            move=False  # Set to True to move instead of copy
        )
        logger.info("Archived to: %s", archive_path)

    logger.info("\n🎉 Ingestion pipeline completed [Batch: %s]", batch_id)
    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Sales Analytics Ingestion")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without making changes")
    parser.add_argument("--skip-archive", action="store_true",
                        help="Skip archiving step")
    parser.add_argument("--since", type=str,
                        help="Process files modified since (YYYY-MM-DD)")

    args = parser.parse_args()

    SINCE_FILTER = None
    if args.since:
        SINCE_FILTER = datetime.strptime(args.since, "%Y-%m-%d")

    SUCCESS = run_ingestion_pipeline(
        dry_run=args.dry_run,
        skip_archive=args.skip_archive,
        since=SINCE_FILTER
    )

    sys.exit(0 if SUCCESS else 1)
