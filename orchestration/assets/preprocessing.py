"""Asset for preprocessing Excel files"""
import shutil
import sys
from datetime import datetime
from pathlib import Path
from ingestion.config.settings import INPUT_PATHS
from ingestion.orchestrate.excel_preprocessor import ExcelPreprocessor
from dagster import asset, MetadataValue, AssetExecutionContext, AssetIn
import pandas as pd
from orchestration.assets.file_discovery import load_processed_files, save_processed_files
from orchestration.utils.constants import DEAD_LETTER_DIR


sys.path.append(str(Path(__file__).parent.parent.parent / "ingestion"))


@asset(
    group_name="ingestion",
    description="Preprocesses Excel files (deletes synthesis sheets, validates)",
    compute_kind="python",
    ins={"files_to_process": AssetIn()}
)
def preprocessed_files(context: AssetExecutionContext,
                       files_to_process: pd.DataFrame) -> pd.DataFrame:
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
    dead_letter_batch = None
    processed_state = load_processed_files()

    def _move_to_dead_letter(source_file: Path) -> str:
        nonlocal dead_letter_batch
        if dead_letter_batch is None:
            dead_letter_batch = DEAD_LETTER_DIR / f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            dead_letter_batch.mkdir(parents=True, exist_ok=True)

        destination = dead_letter_batch / source_file.name
        try:
            shutil.move(str(source_file), str(destination))
            context.log.warning("Moved failed file to dead_letter: %s", destination)
            return str(destination)
        except Exception as move_exc:
            context.log.error(
                "Failed to move %s to dead_letter: %s",
                source_file.name,
                move_exc,
            )
            return ""

    def _remove_partial_target(target_file: Path) -> None:
        if not target_file.exists():
            return

        try:
            target_file.unlink()
            context.log.warning("Removed partial input file: %s", target_file)
        except OSError as cleanup_exc:
            context.log.error(
                "Failed to remove partial input file %s: %s",
                target_file,
                cleanup_exc,
            )

    for idx, row in files_to_process.iterrows():
        source_path = Path(row["file_path"])
        source_type = row["source_type"]
        target_dir = INPUT_PATHS[source_type]
        target_path = target_dir / source_path.name

        if str(source_path) in processed_state:
            context.log.info(
                f"Skipping {source_path.name} - already recorded in state"
            )
            processed_records.append({
                "source_type": source_type,
                "file_name": source_path.name,
                "file_path": str(source_path),
                "status": "skipped",
                "sheets_deleted": 0,
                "target_path": str(target_path),
            })
            continue

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
                        "file_path": str(source_path),
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
                    "file_path": str(source_path),
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
                _remove_partial_target(target_path)
                dead_path = _move_to_dead_letter(source_path)
                processed_records.append({
                    "source_type": source_type,
                    "file_name": source_path.name,
                    "file_path": str(source_path),
                    "status": "error",
                    "sheets_deleted": 0,
                    "error": error_msg,
                    "target_path": "",
                    "dead_letter_path": dead_path,
                })
                context.log.error(
                    f"❌ Failed to process {source_path.name}: {error_msg}")

        except Exception as e:
            context.log.error(f"❌ Failed to process {source_path.name}: {e}")
            _remove_partial_target(target_path)
            dead_path = _move_to_dead_letter(source_path)
            processed_records.append({
                "source_type": source_type,
                "file_name": source_path.name,
                "file_path": str(source_path),
                "status": "error",
                "sheets_deleted": 0,
                "error": str(e),
                "target_path": "",
                "dead_letter_path": dead_path,
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

    # Persist state for successfully processed/skipped files only.
    if not result_df.empty:
        processed_paths = set(
            result_df[result_df["status"].isin(["processed", "skipped"])]["file_path"]
        ) if "file_path" in result_df.columns else set()

        if processed_paths:
            current_state = load_processed_files()
            updated_state = current_state | set(str(path) for path in processed_paths)
            save_processed_files(updated_state)
            context.log.info(
                f"Updated state: {len(updated_state)} total processed files")

    context.add_output_metadata({
        "row_count": len(result_df),
        "processed": processed_count,
        "skipped": skipped_count,
        "errors": error_count,
        "total_sheets_deleted": int(total_sheets_deleted),
        "dead_letter_files": len(result_df[result_df["status"] == "error"])
            if not result_df.empty else 0,
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
