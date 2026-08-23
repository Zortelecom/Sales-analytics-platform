"""
orchestration/assets/preprocessing.py

Clean each discovered workbook and place it in data/input/<source_type>/.

(2026-08) Processing rules come from sources.yaml.

The hardcoded if/elif on source_type was a second, competing copy of the
`processing:` block in sources.yaml, and the two had already drifted: the yaml
said the sales delete pattern was "Synthese*" while this file used
"Synthese *", which misses every "SyntheseJan"-style sheet. Nothing failed,
because SourceConfig.processing_rules was loaded and never read.

tests/orchestration/test_no_hardcoded_source_rules.py fails if a
`source_type == "..."` comparison reappears here.

(2026-08b) FAILURE HANDLING NO LONGER EDITS THE SUPERVISORS' FOLDER
──────────────────────────────────────────────────────────────────
It used to do this on any preprocessing error:

    shutil.move(str(source_file), str(destination))

source_file is under the path sources.yaml points at -- the SYNCED folder the
supervisors work in. A move propagates as a DELETE to every other copy of that
folder. So a workbook disappeared from a supervisor's own machine because an
extractor did not like a sheet name, with no notification and no obvious way
for them to get it back. The pipeline is a READER of that directory; it has no
business mutating it.

It copies now. The consequence is that a failing workbook stays discoverable
and would fail again on every run -- which is why quarantine exists:

    file fails            -> copy to dead_letter, record {path: sha256}
    next run, unchanged   -> status "quarantined", skipped, no retry
    supervisor edits it   -> hash differs, quarantine cleared, retried

Same mechanism as processed-state hashing, same reason: content is the only
thing that reliably says whether a file is the one that already failed.

"quarantined" is deliberately NOT in ingestion.EXTRACTABLE_STATUSES, so a
quarantined file does not gate extraction open the way "skipped" does.
"""
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd
from dagster import AssetExecutionContext, AssetIn, MetadataValue, asset

from ingestion.orchestrate.excel_preprocessor import ExcelPreprocessor
from orchestration.assets.file_discovery import (
    clear_quarantine,
    file_sha256,
    is_quarantined,
    load_processed_state,
    record_quarantine,
    save_processed_state,
)
from orchestration.utils.constants import DEAD_LETTER_DIR
from shared.sources import load_sources

# Every record carries every key, so the resulting frame has no NaN-filled
# columns and downstream `.isin([...])` checks on status are total.
_RECORD_FIELDS = (
    "source_type",
    "file_name",
    "file_path",
    "status",
    "sheets_deleted",
    "target_path",
    "error",
    "dead_letter_path",
)


def _record(**kwargs) -> dict:
    """A processing record with all fields present."""
    record = {field: "" for field in _RECORD_FIELDS}
    record["sheets_deleted"] = 0
    record.update(kwargs)
    return record


@asset(
    group_name="ingestion",
    description="Preprocesses Excel workbooks (deletes synthesis sheets, validates)",
    compute_kind="python",
    ins={"files_to_process": AssetIn()},
)
def preprocessed_files(
    context: AssetExecutionContext, files_to_process: pd.DataFrame
) -> pd.DataFrame:
    """
    Clean and copy workbooks into the input directory.

    Per file:
      1. Skip if content is unchanged since the last successful preprocess.
      2. Skip if this exact content already failed (quarantine).
      3. Delete unwanted sheets per sources.yaml.
      4. Validate required sheets.
      5. Write the cleaned copy to data/input/<source_type>/.

    Status is one of: processed, skipped, quarantined, error.
    """
    if files_to_process.empty:
        context.log.info("No files to preprocess")
        return pd.DataFrame()

    records: list[dict] = []
    preprocessor = ExcelPreprocessor(dry_run=False)
    registry = load_sources()
    processed_state = load_processed_state()
    dead_letter_batch: Path | None = None

    def _copy_to_dead_letter(source_file: Path) -> str:
        """
        COPY, never move. See the module docstring -- the source directory is
        the supervisors' synced folder and a move deletes their file.
        """
        nonlocal dead_letter_batch
        if dead_letter_batch is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dead_letter_batch = DEAD_LETTER_DIR / f"batch_{stamp}"
            dead_letter_batch.mkdir(parents=True, exist_ok=True)

        destination = dead_letter_batch / source_file.name
        try:
            shutil.copy2(str(source_file), str(destination))
            context.log.warning("Copied failed file to dead_letter: %s", destination)
            return str(destination)
        except OSError as copy_exc:
            context.log.error(
                "Could not copy %s to dead_letter: %s", source_file.name, copy_exc
            )
            return ""

    def _remove_partial_target(target_file: Path) -> None:
        """A half-written cleaned copy is worse than none: extractors read it."""
        if not target_file.exists():
            return
        try:
            target_file.unlink()
            context.log.warning("Removed partial input file: %s", target_file)
        except OSError as cleanup_exc:
            context.log.error(
                "Could not remove partial input file %s: %s", target_file, cleanup_exc
            )

    def _fail(source_path: Path, source_type: str, target_path: Path, message: str) -> None:
        """Quarantine a workbook at its current content and record the failure."""
        _remove_partial_target(target_path)
        dead_path = _copy_to_dead_letter(source_path)
        try:
            record_quarantine(source_path, file_sha256(source_path))
        except OSError:
            # Unreadable now: leave it out of quarantine so it is retried
            # rather than skipped on a hash we could not compute.
            pass
        records.append(_record(
            source_type=source_type,
            file_name=source_path.name,
            file_path=str(source_path),
            status="error",
            error=message,
            dead_letter_path=dead_path,
        ))
        context.log.error("Failed to process %s: %s", source_path.name, message)

    for _, row in files_to_process.iterrows():
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
            records.append(_record(
                source_type=source_type,
                file_name=source_path.name,
                file_path=str(source_path),
                status="skipped",
                target_path=str(target_path),
            ))
            continue

        if is_quarantined(source_path, source_hash):
            context.log.warning(
                "Skipping %s - this exact content already failed. It will be "
                "retried automatically once the workbook is edited.",
                source_path.name,
            )
            records.append(_record(
                source_type=source_type,
                file_name=source_path.name,
                file_path=str(source_path),
                status="quarantined",
                error="Unchanged since a previous preprocessing failure",
            ))
            continue

        # Content differs from whatever was quarantined, so give it a fresh
        # chance and forget the old verdict.
        clear_quarantine(source_path)

        # From sources.yaml. Do NOT reintroduce a branch on source_type here.
        delete_pattern = spec.processing.delete_sheets_pattern
        required_sheets = spec.processing.required_sheets

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            context.log.info("Processing %s...", source_path.name)

            # Reset stats for this file
            preprocessor.stats = {"processed": 0, "sheets_deleted": 0, "errors": []}

            success = preprocessor.preprocess(
                source_file=source_path,
                target_file=target_path,
                delete_pattern=delete_pattern,
                required_sheets=required_sheets,
            )

            if success:
                sheets_deleted = preprocessor.stats.get("sheets_deleted", 0)
                records.append(_record(
                    source_type=source_type,
                    file_name=source_path.name,
                    file_path=str(source_path),
                    status="processed",
                    sheets_deleted=sheets_deleted,
                    target_path=str(target_path),
                ))
                context.log.info(
                    "Preprocessed %s -> %s (%d sheet(s) deleted)",
                    source_path.name, target_path.name, sheets_deleted,
                )
            else:
                errors = preprocessor.stats.get("errors") or ["Unknown error"]
                _fail(source_path, source_type, target_path, str(errors[0]))

        except Exception as exc:  # noqa: BLE001 -- one bad workbook, not the run
            _fail(source_path, source_type, target_path, str(exc))

    result_df = pd.DataFrame(records)

    def _count(status: str) -> int:
        if result_df.empty:
            return 0
        return int((result_df["status"] == status).sum())

    processed_count = _count("processed")
    skipped_count = _count("skipped")
    quarantined_count = _count("quarantined")
    error_count = _count("error")
    total_sheets_deleted = (
        int(result_df["sheets_deleted"].sum()) if not result_df.empty else 0
    )

    # Record the CONTENT HASH of every source that made it through, so the next
    # run can tell "unchanged" from "merely older". Quarantined and errored
    # files are deliberately absent: the quarantine manifest tracks those, and
    # recording them here would make a fixed workbook look already-processed.
    if not result_df.empty:
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
        "quarantined": quarantined_count,
        "errors": error_count,
        "total_sheets_deleted": total_sheets_deleted,
        "preview": MetadataValue.md(
            result_df[["source_type", "file_name", "status", "sheets_deleted"]]
            .to_markdown(index=False)
            if not result_df.empty else "No records"
        ),
    })

    context.log.info("=" * 70)
    context.log.info("Preprocessing summary")
    context.log.info("  Processed:      %d", processed_count)
    context.log.info("  Skipped:        %d", skipped_count)
    context.log.info("  Quarantined:    %d", quarantined_count)
    context.log.info("  Errors:         %d", error_count)
    context.log.info("  Sheets deleted: %d", total_sheets_deleted)
    context.log.info("=" * 70)

    if quarantined_count:
        context.log.warning(
            "%d workbook(s) skipped as unchanged-since-failure. They will be "
            "retried automatically once edited; see data/dead_letter/.",
            quarantined_count,
        )

    return result_df