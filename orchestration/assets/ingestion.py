"""Asset for extracting data to SQLMesh seeds"""

import sys
from pathlib import Path
from datetime import datetime
from orchestration.utils.constants import SEEDS_DIR
from ingestion.load.seed_writer import SeedWriter
from ingestion.extract.reference_extractor import ReferenceExtractor
from ingestion.extract.target_extractor import TargetExtractor
from ingestion.extract.sales_extractor import SalesExtractor
from ingestion.extract.kp_sd_extractor import KPDestockeExtractor, KPNonDestockeExtractor
from ingestion.config.settings import INPUT_PATHS
from dagster import asset, MetadataValue, AssetExecutionContext, Failure, AssetIn
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent.parent / "ingestion"))


@asset(
    group_name="ingestion",
    description="Generate current batch ID based on timestamp"
)
def current_batch_id(context: AssetExecutionContext) -> str:
    """
    Generate unique batch identifier for this ingestion run.

    Format: YYYYMMDD_HHMMSS (e.g., "20250205_143022")

    This batch ID is used to track all data extracted in this run
    across all seed files.
    """
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    context.add_output_metadata({
        "batch_id": batch_id,
        "generated_at": datetime.now().isoformat(),
    })

    context.log.info(f"Generated batch ID: {batch_id}")
    return batch_id


@asset(
    group_name="ingestion",
    description="Extracts sales data from preprocessed Excel files to CSV seeds",
    compute_kind="python",
    ins={"preprocessed_files": AssetIn(), "current_batch_id": AssetIn()}
)
def sales_seed(
    context: AssetExecutionContext,
    preprocessed_files: pd.DataFrame,
    current_batch_id: str  # ✅ FIXED: Match asset name
) -> pd.DataFrame:
    """
    Extract sales data from Excel files and write to SQLMesh seed.

    Reads from: data/input/sales/*.xlsx
    Writes to: sqlmesh/seeds/sales_data.csv

    Expected Excel structure:
    - Multiple files: ExSD-Sales-{SubRegion}.xlsx
    - Each file has sheets named after salespersons
    - Each sheet has tables prefixed with "Sales"

    Returns:
        DataFrame with extracted sales data
    """

    if preprocessed_files.empty:
        context.log.warning("No preprocessed files available")
        return pd.DataFrame()

    # Filter for successfully processed sales files
    sales_files = preprocessed_files[
        (preprocessed_files["source_type"] == "sales") &
        (preprocessed_files["status"] == "processed")
    ]

    if sales_files.empty:
        context.log.warning("No sales files were successfully preprocessed")
        return pd.DataFrame()

    context.log.info(f"Extracting from {len(sales_files)} sales file(s)...")

    try:
        # Initialize extractor with batch ID
        extractor = SalesExtractor(batch_id=current_batch_id)

        # Extract from directory (reads all files in data/input/sales/)
        df = extractor.read(INPUT_PATHS["sales"])

        if df.empty:
            raise ValueError("No sales data extracted from files")

        # Write seed
        writer = SeedWriter(SEEDS_DIR, current_batch_id)
        seed_path = writer.write_seed(df, "sales_data")

        # Preview for metadata
        preview_df = df.head(
            5)[['salesperson_id', 'sd_id', 'sale_date', 'sku', 'qty', 'amount']]

        context.add_output_metadata({
            "row_count": len(df),
            "columns": len(df.columns),
            "unique_salespersons": int(df['salesperson_id'].nunique()) if 'salesperson_id' in df.columns else 0,
            "unique_clients": int(df['sd_id'].nunique()) if 'sd_id' in df.columns else 0,
            "seed_path": str(seed_path),
            "preview": MetadataValue.md(preview_df.to_markdown()),
        })

        context.log.info(f"✅ Sales seed created: {len(df):,} rows")
        return df

    except Exception as e:
        context.log.error(f"Sales extraction failed: {e}", exc_info=True)
        raise Failure(description=f"Sales extraction failed: {e}") from e


@asset(
    group_name="ingestion",
    description="Extracts targets data to CSV seeds",
    compute_kind="python",
    ins={"preprocessed_files": AssetIn(), "current_batch_id": AssetIn()}
)
def targets_seed(
    context: AssetExecutionContext,
    preprocessed_files: pd.DataFrame,
    current_batch_id: str  # ✅ FIXED: Match asset name
) -> pd.DataFrame:
    """
    Extract targets data from Excel files and write to SQLMesh seed.

    Reads from: data/input/targets/Sales_Targets.xlsx
    Writes to: sqlmesh/seeds/targets_data.csv

    Expected Excel structure:
    - Single file: Sales_Targets.xlsx
    - Table prefixed with "Target"
    - Columns: month_year, salesperson_id, product_category, target_amount

    Returns:
        DataFrame with extracted targets data
    """

    if preprocessed_files.empty:
        context.log.warning("No preprocessed files available")
        return pd.DataFrame()

    # Filter for successfully processed targets files
    targets_files = preprocessed_files[
        (preprocessed_files["source_type"] == "targets") &
        (preprocessed_files["status"] == "processed")
    ]

    if targets_files.empty:
        context.log.warning("No targets files were successfully preprocessed")
        return pd.DataFrame()

    context.log.info(
        f"Extracting from {len(targets_files)} targets file(s)...")

    try:
        # Initialize extractor with batch ID
        extractor = TargetExtractor(batch_id=current_batch_id)

        # Extract from directory
        df = extractor.read(INPUT_PATHS["targets"])

        if df.empty:
            raise ValueError("No targets data extracted from files")

        # Write seed
        writer = SeedWriter(SEEDS_DIR, current_batch_id)
        seed_path = writer.write_seed(df, "targets_data")

        # Preview for metadata
        preview_df = df.head(
            5)[['month_year', 'salesperson_id', 'product_category', 'target_amount']]

        context.add_output_metadata({
            "row_count": len(df),
            "columns": len(df.columns),
            "unique_salespersons": int(df['salesperson_id'].nunique()) if 'salesperson_id' in df.columns else 0,
            "unique_categories": int(df['product_category'].nunique()) if 'product_category' in df.columns else 0,
            "seed_path": str(seed_path),
            "preview": MetadataValue.md(preview_df.to_markdown()),
        })

        context.log.info(f"✅ Targets seed created: {len(df):,} rows")
        return df

    except Exception as e:
        context.log.error(f"Targets extraction failed: {e}", exc_info=True)
        raise Failure(description=f"Targets extraction failed: {e}") from e


@asset(
    group_name="ingestion",
    description="Extracts reference data (clients, products, team) to CSV seeds",
    compute_kind="python",
    ins={"preprocessed_files": AssetIn(), "current_batch_id": AssetIn()}
)
def references_seeds(
    context: AssetExecutionContext,
    preprocessed_files: pd.DataFrame,
    current_batch_id: str  # ✅ FIXED: Match asset name
) -> dict:
    """
    Extract all reference data from Excel files and write to SQLMesh seeds.

    Reads from: data/input/references/References.xlsx
    Writes to:
    - sqlmesh/seeds/salesteam_data.csv
    - sqlmesh/seeds/products_data.csv
    - sqlmesh/seeds/clientSD_data.csv

    Expected Excel structure:
    - Single file: References.xlsx
    - Sheets: Ref_Salesteam, Ref_Products, Ref_ClientsSD

    Returns:
        Dictionary with extraction results for each reference type
    """

    if preprocessed_files.empty:
        context.log.warning("No preprocessed files available")
        return {}

    # Filter for successfully processed references files
    ref_files = preprocessed_files[
        (preprocessed_files["source_type"] == "references") &
        (preprocessed_files["status"] == "processed")
    ]

    if ref_files.empty:
        context.log.warning(
            "No reference files were successfully preprocessed")
        return {}

    context.log.info(f"Extracting from {len(ref_files)} reference file(s)...")

    try:
        # Initialize extractor with batch ID
        extractor = ReferenceExtractor(batch_id=current_batch_id)

        # Extract from directory (returns dict of DataFrames)
        results = extractor.read(INPUT_PATHS["references"])

        if not results:
            raise ValueError("No reference data extracted from files")

        # Initialize seed writer
        writer = SeedWriter(SEEDS_DIR, current_batch_id)

        # Map internal names to seed names
        seed_mapping = {
            'ref_salesteam': 'salesteam_data',
            'ref_products': 'products_data',
            'ref_clients_sd': 'clientSD_data',
            'ref_kp_sku_mapping': 'kp_sku_mapping_data',
        }

        output_results = {}

        for key, df in results.items():
            if key in seed_mapping and not df.empty:
                seed_name = seed_mapping[key]

                # Write seed
                seed_path = writer.write_seed(df, seed_name)

                output_results[key] = {
                    "seed_name": seed_name,
                    "rows": len(df),
                    "columns": len(df.columns),
                    "path": str(seed_path),
                }

                context.log.info(f"✅ {seed_name}: {len(df):,} rows")

        if not output_results:
            raise ValueError("No reference seeds were created")

        context.add_output_metadata({
            "extracted_references": output_results,
            "total_rows": sum(r["rows"] for r in output_results.values()),
            "total_seeds": len(output_results),
        })

        return output_results

    except Exception as e:
        context.log.error(f"References extraction failed: {e}", exc_info=True)
        raise Failure(description=f"References extraction failed: {e}") from e


@asset(
    group_name="ingestion",
    description="Extract KP sell-in workbooks to SQLMesh seeds",
    compute_kind="python",
    ins={"preprocessed_files": AssetIn(), "current_batch_id": AssetIn()},
)
def kp_sd_seed(
    context: AssetExecutionContext,
    preprocessed_files: pd.DataFrame,
    current_batch_id: str,
) -> dict:
    """Write separate destocked and non-destocked KP sell-in seeds."""
    files = preprocessed_files[
        (preprocessed_files["source_type"] == "kp_sd")
        & (preprocessed_files["status"] == "processed")
    ] if not preprocessed_files.empty else pd.DataFrame()
    if files.empty:
        context.log.warning("No KP-SD files were successfully preprocessed")
        return {}

    writer = SeedWriter(SEEDS_DIR, current_batch_id)
    result = {}
    for seed_name, extractor in (
        ("kp_sd_destocke_data", KPDestockeExtractor(current_batch_id)),
        ("kp_sd_non_destocke_data", KPNonDestockeExtractor(current_batch_id)),
    ):
        df = extractor.read(INPUT_PATHS["kp_sd"])
        if df.empty:
            context.log.warning("No rows extracted for %s", seed_name)
            continue
        path = writer.write_seed(df, seed_name)
        result[seed_name] = {"rows": len(df), "path": str(path)}

    if not result:
        raise Failure(description="KP-SD files were found but no sell-in rows could be extracted")
    context.add_output_metadata({"seeds": result, "total_rows": sum(x["rows"] for x in result.values())})
    return result


@asset(
    group_name="ingestion",
    description="Metadata about all seeds generated in this batch",
    ins={
        "sales_seed": AssetIn(),
        "targets_seed": AssetIn(),
        "references_seeds": AssetIn(),
        "kp_sd_seed": AssetIn(),
        "current_batch_id": AssetIn()
    }
)
def seeds_metadata(
    context: AssetExecutionContext,
    sales_seed: pd.DataFrame,  # ✅ FIXED: Match asset name
    targets_seed: pd.DataFrame,  # ✅ FIXED: Match asset name
    references_seeds: dict,
    kp_sd_seed: dict,
    current_batch_id: str
) -> dict:
    """
    Aggregate metadata about all seeds created in this batch.

    This asset serves as a final checkpoint before transformation,
    ensuring all seeds were created successfully.

    Returns:
        Dictionary with batch summary statistics
    """

    # Extract batch_id from sales data (it's in the ingestion_batch_id column)
    batch_id = "unknown"
    if not sales_seed.empty and 'ingestion_batch_id' in sales_seed.columns:
        batch_id = sales_seed['ingestion_batch_id'].iloc[0]

    metadata = {
        "batch_id": current_batch_id,
        "timestamp": datetime.now().isoformat(),
        "seeds_path": str(SEEDS_DIR),
        "sales_rows": len(sales_seed),
        "targets_rows": len(targets_seed),
        "kp_sd_rows": sum(v["rows"] for v in kp_sd_seed.values()),
        "reference_seeds": {
            k: v["rows"] for k, v in references_seeds.items()
        },
        "total_reference_rows": sum(v["rows"] for v in references_seeds.values()),
        "total_rows": len(sales_seed) + len(targets_seed) + sum(v["rows"] for v in references_seeds.values()) + sum(v["rows"] for v in kp_sd_seed.values()),
    }

    context.add_output_metadata(metadata)

    # Log summary
    context.log.info("="*70)
    context.log.info("Ingestion Batch Summary:")
    context.log.info(f"  Batch ID: {batch_id}")
    context.log.info(f"  Sales:    {len(sales_seed):,} rows")
    context.log.info(f"  Targets:  {len(targets_seed):,} rows")
    for ref_name, ref_data in references_seeds.items():
        context.log.info(
            f"  {ref_data['seed_name']:12s}: {ref_data['rows']:,} rows")
    context.log.info(f"  TOTAL:    {metadata['total_rows']:,} rows")
    context.log.info("="*70)

    return metadata
