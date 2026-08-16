"""
Asset for preprocessing Excel files.

(2026-08) Processing rules come from sources.yaml.

The hardcoded if/elif on source_type was a second, competing copy of the
`processing:` block in sources.yaml, and the two had already drifted: the yaml
said the sales delete pattern was "Synthese*" while this file used
"Synthese *", which misses every "SyntheseJan"-style sheet. Nothing failed,
because SourceConfig.processing_rules was loaded and never read.

tests/orchestration/test_no_hardcoded_source_rules.py fails if a
`source_type == "..."` comparison reappears here.
"""
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd
from dagster import AssetExecutionContext, AssetIn, MetadataValue, asset

from ingestion.orchestrate.excel_preprocessor import ExcelPreprocessor
from orchestration.assets.file_discovery import (
    file_sha256,
    load_processed_state,
    save_processed_state,
)
from orchestration.utils.constants import DEAD_LETTER_DIR
from shared.sources import load_sources


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
    registry = load_sources()
    processed_state = load_processed_state()
    processed_state = load_processed_state()

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
        # Raises a listing of declared source types on an unknown one, rather
        # than a bare KeyError.
        spec = registry[source_type]
        target_dir = spec.input_dir
        target_path = target_dir / source_path.name

        # Content, not mtime. `target_mtime >= source_mtime` silently skipped
        # three of four KP workbooks that had just gained July rows, because
        # the copies in data/input/ happened to be newer than the edits.
        try:
            source_hash = file_sha256(source_path)
        except OSError as exc:
            context.log.error("Cannot hash %s: %s", source_path, exc)
            source_hash = ""

        if source_hash and processed_state.get(str(source_path)) == source_hash \
                and target_path.exists():
            context.log.info(
                "Skipping %s - content unchanged since last preprocess",
                source_path.name,
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

        # From sources.yaml. Do NOT reintroduce a branch on source_type here.
        delete_pattern = spec.processing.delete_sheets_pattern
        required_sheets = spec.processing.required_sheets

        try:
            # Ensure target directory exists
            target_dir.mkdir(parents=True, exist_ok=True)

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

    # Record the CONTENT HASH of every source that made it through, so the
    # next run can tell "unchanged" from "merely older".
    if not result_df.empty and "file_path" in result_df.columns:
        ok = result_df[result_df["status"].isin(["processed", "skipped"])]
        if not ok.empty:
            state = load_processed_state()
            for path in ok["file_path"]:
                try:
                    state[str(path)] = file_sha256(Path(path))
                except OSError:
                    state.pop(str(path), None)   # re-check it next time
            save_processed_state(state)
            context.log.info("State: %d source file(s) recorded", len(state))

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