"""Asset for preprocessing Excel files"""
import sys
from pathlib import Path
from ingestion.config.settings import INPUT_PATHS
from ingestion.orchestrate.excel_preprocessor import ExcelPreprocessor
from dagster import asset, MetadataValue, AssetExecutionContext, AssetIn
import pandas as pd


sys.path.append(str(Path(__file__).parent.parent.parent / "ingestion"))


@asset(
    group_name="ingestion",
    description="Preprocesses Excel files (deletes synthesis sheets, validates)",
    compute_kind="python",
    ins={"files_to_process": AssetIn()}
)
def preprocessed_files(context: AssetExecutionContext, files_to_process: pd.DataFrame) -> pd.DataFrame:
    """
    Clean and move files to input directory.
    
    For each file:
    1. Delete unwanted sheets (e.g., "Synthese *" for sales files)
    2. Validate required sheets exist
    3. Copy cleaned file to data/input/{source_type}/
    
    Returns:
        DataFrame with processing results:
        - source_type: File category
        - file_name: Original filename
        - status: 'processed', 'skipped', or 'error'
        - sheets_deleted: Number of sheets removed
        - target_path: Path to cleaned file
        - error: Error message (if status='error')
    """

    if files_to_process.empty:
        context.log.info("No files to preprocess")
        return pd.DataFrame()

    processed_records = []
    preprocessor = ExcelPreprocessor(dry_run=False)

    for idx, row in files_to_process.iterrows():
        source_path = Path(row["file_path"])
        source_type = row["source_type"]
        target_dir = INPUT_PATHS[source_type]
        target_path = target_dir / source_path.name

        # Determine processing rules based on source_type
        delete_pattern = None
        required_sheets = None

        if source_type == "sales":
            delete_pattern = "Synthese *"
            required_sheets = ["Sales*"]
        elif source_type == "targets":
            required_sheets = ["Target*"]
        # references files have no special processing

        try:
            # Ensure target directory exists
            target_dir.mkdir(parents=True, exist_ok=True)

            # Check if file already exists and is up to date
            if target_path.exists():
                source_mtime = source_path.stat().st_mtime
                target_mtime = target_path.stat().st_mtime

                if target_mtime >= source_mtime:
                    context.log.info(
                        f"⏭️  Skipping {source_path.name} - already current "
                        f"(target newer than source)"
                    )
                    processed_records.append({
                        "source_type": source_type,
                        "file_name": source_path.name,
                        "status": "skipped",
                        "sheets_deleted": 0,
                        "target_path": str(target_path),
                    })
                    continue

            # Preprocess the file
            context.log.info(f"Processing {source_path.name}...")

            # Reset stats for this file
            preprocessor.stats = {
                "processed": 0,
                "sheets_deleted": 0,
                "errors": []
            }

            success = preprocessor.preprocess(
                source_file=source_path,
                target_file=target_path,
                delete_pattern=delete_pattern,
                required_sheets=required_sheets
            )

            if success:
                sheets_deleted = preprocessor.stats.get("sheets_deleted", 0)

                processed_records.append({
                    "source_type": source_type,
                    "file_name": source_path.name,
                    "status": "processed",
                    "sheets_deleted": sheets_deleted,
                    "target_path": str(target_path),
                })

                context.log.info(
                    f"✅ Preprocessed {source_path.name} → {target_path.name} "
                    f"({sheets_deleted} sheets deleted)"
                )
            else:
                error_msg = preprocessor.stats.get("errors", ["Unknown error"])[
                    0] if preprocessor.stats.get("errors") else "Unknown error"
                processed_records.append({
                    "source_type": source_type,
                    "file_name": source_path.name,
                    "status": "error",
                    "sheets_deleted": 0,
                    "error": error_msg,
                    "target_path": "",
                })
                context.log.error(
                    f"❌ Failed to process {source_path.name}: {error_msg}")

        except (OSError, ValueError, KeyError) as e:
            context.log.error(f"❌ Failed to process {source_path.name}: {e}")
            processed_records.append({
                "source_type": source_type,
                "file_name": source_path.name,
                "status": "error",
                "sheets_deleted": 0,
                "error": str(e),
                "target_path": "",
            })

    result_df = pd.DataFrame(processed_records)

    # Summary statistics
    processed_count = len(
        result_df[result_df["status"] == "processed"]) if not result_df.empty else 0
    skipped_count = len(
        result_df[result_df["status"] == "skipped"]) if not result_df.empty else 0
    error_count = len(result_df[result_df["status"]
                      == "error"]) if not result_df.empty else 0
    total_sheets_deleted = result_df["sheets_deleted"].sum(
    ) if not result_df.empty else 0

    context.add_output_metadata({
        "row_count": len(result_df),
        "processed": processed_count,
        "skipped": skipped_count,
        "errors": error_count,
        "total_sheets_deleted": int(total_sheets_deleted),
        "preview": MetadataValue.md(
            result_df[["source_type", "file_name",
                       "status", "sheets_deleted"]].to_markdown()
            if not result_df.empty else "No records"
        ),
    })

    # Log summary
    context.log.info("="*70)
    context.log.info("Preprocessing Summary:")
    context.log.info(f"  ✅ Processed: {processed_count}")
    context.log.info(f"  ⏭️  Skipped:   {skipped_count}")
    context.log.info(f"  ❌ Errors:    {error_count}")
    context.log.info(f"  🗑️  Sheets deleted: {total_sheets_deleted}")
    context.log.info("="*70)

    return result_df
