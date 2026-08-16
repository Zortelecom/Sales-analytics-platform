"""Asset for discovering source files"""

from datetime import datetime, timedelta
from pathlib import Path
import sys
import hashlib
import json
import tempfile
import pandas as pd
from ingestion.orchestrate.file_discovery import FileDiscovery as FileDiscoveryClass
from ingestion.config.settings import load_sources_config
from orchestration.utils.constants import DEAD_LETTER_DIR, STATE_FILE
from dagster import (
    asset,
    asset_check,
    AssetCheckResult,
    AssetCheckSeverity,
    MetadataValue,
    AssetExecutionContext,
    AssetIn,
)

# Add ingestion to path for imports
sys.path.append(str(Path(__file__).parent.parent.parent / "ingestion"))

def file_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    """Content hash of a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def load_processed_state() -> dict:
    """
    Return {source_path: sha256} for files already handled.

    (2026-08) The state used to be a SET of paths and preprocessing decided
    whether to redo a file by comparing MTIMES:

        if target_mtime >= source_mtime: skip

    That is a heuristic, and it failed silently the first time it mattered.
    Four KP workbooks gained July rows; the copies in data/input/ happened to
    have later mtimes than the edits, so three of the four were skipped, the
    extractors read yesterday's workbooks, and the new rows never reached the
    lake. Nothing errored -- 999 rows landed instead of thousands.

    Hashing the CONTENT is the same mechanism landing already uses for
    supersession, and it cannot be fooled by a timestamp.
    """
    if not STATE_FILE.exists():
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}

    if isinstance(state, dict) and "processed_files" in state:
        state = state["processed_files"]
    # Migrate the old list/set form: unknown hashes force one re-process,
    # which is the safe direction.
    if isinstance(state, list):
        return {path: "" for path in state}
    return state if isinstance(state, dict) else {}


def save_processed_state(state: dict) -> None:
    """Persist {source_path: sha256} atomically."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=str(STATE_FILE.parent), suffix=".tmp",
    ) as temp_file:
        json.dump(state, temp_file, indent=2)
        temp_path = Path(temp_file.name)
    temp_path.replace(STATE_FILE)


def load_processed_files() -> set:
    """Back-compat: just the paths. Prefer load_processed_state()."""
    return set(load_processed_state())


def save_processed_files(files: set) -> None:
    """Back-compat shim; records an unknown hash so the file is re-checked."""
    state = load_processed_state()
    state.update({str(path): state.get(str(path), "") for path in files})
    save_processed_state(state)


@asset(
    group_name="ingestion",
    description="Discovers new Excel files in source directories",
    compute_kind="python"
)
def discovered_files(context: AssetExecutionContext) -> pd.DataFrame:
    """
    Scan source directories and return manifest of files to process.
    
    Returns a DataFrame with columns:
    - source_type: 'sales', 'targets', or 'references'
    - file_name: Name of the file
    - file_path: Absolute path to the file
    - size_bytes: File size in bytes
    - modified_time: Last modification timestamp
    - discovered_at: Current timestamp
    """

    processed_files = load_processed_files()

    config = load_sources_config()
    discovery = FileDiscoveryClass(config)
    all_files = discovery.discover_all()

    files_data = []

    for source_type, files in all_files.items():
        for file_path in files:
            stat = file_path.stat()
            files_data.append({
                "source_type": source_type,
                "file_name": file_path.name,
                "file_path": str(file_path),
                "size_bytes": stat.st_size,
                "size_mb": stat.st_size / (1024 * 1024),
                "modified_time": datetime.fromtimestamp(stat.st_mtime),
                "discovered_at": datetime.now(),
            })

    df = pd.DataFrame(files_data)

    # Metadata for Dagster UI
    context.add_output_metadata({
        "row_count": len(df),
        "preview": MetadataValue.md(df.head(10).to_markdown() if not df.empty else "No files found"),
        "sales_files": len(df[df["source_type"] == "sales"]) if not df.empty else 0,
        "targets_files": len(df[df["source_type"] == "targets"]) if not df.empty else 0,
        "references_files": len(df[df["source_type"] == "references"]) if not df.empty else 0,
        "total_size_mb": f"{df['size_mb'].sum():.2f}" if not df.empty else "0",
        "state_file": MetadataValue.path(str(STATE_FILE)),
        "processed_files_in_state": len(load_processed_state()),
    })

    context.log.info(
        f"Discovered {len(df)} files "
        f"({len(processed_files)} already recorded in state)"
    )
    return df


@asset(
    group_name="ingestion",
    description="Filters files that need processing (new or modified since last run)",
    ins={"discovered_files": AssetIn()}
)
def files_to_process(context: AssetExecutionContext, discovered_files: pd.DataFrame) -> pd.DataFrame:
    """
    Filter for files that haven't been processed yet.
    
    Uses persistent state tracking to avoid reprocessing files.
    Falls back to 24-hour lookback if no state exists.
    
    Returns:
        DataFrame with same schema as discovered_files, filtered to new files only
    """

    if discovered_files.empty:
        context.log.info("No files discovered")
        return pd.DataFrame()

    # Load processed files state
    state = load_processed_state()

    if state:
        # Content, not mtime. A workbook whose bytes changed is new, whatever
        # its timestamp says -- see load_processed_state for what mtime cost.
        def _is_new(row) -> bool:
            recorded = state.get(row["file_path"])
            if not recorded:
                return True
            try:
                return file_sha256(Path(row["file_path"])) != recorded
            except OSError:
                return True

        to_process = discovered_files[
            discovered_files.apply(_is_new, axis=1)
        ].copy()
        context.log.info(
            "Content-based filtering: %d changed file(s) of %d discovered",
            len(to_process), len(discovered_files),
        )
    else:
        cutoff_time = datetime.now() - timedelta(hours=24)
        to_process = discovered_files[
            discovered_files["modified_time"] > cutoff_time
        ].copy()
        context.log.warning(
            "No state file found, using 24h lookback: %d file(s)", len(to_process)
        )

    context.add_output_metadata({
        "row_count": len(to_process),
        "new_files": len(to_process),
        "previously_processed": len(state),
        "preview": MetadataValue.md(
            to_process[["source_type", "file_name",
                        "modified_time"]].to_markdown()
            if not to_process.empty else "No new files to process"
        ),
    })

    if to_process.empty:
        context.log.info("No new files to process")
    else:
        context.log.info(f"Files to process: {len(to_process)}")
        for _, row in to_process.iterrows():
            context.log.info(f"  - [{row['source_type']}] {row['file_name']}")

    return to_process


@asset_check(
    asset="discovered_files",
    name="dead_letter_queue_check",
    description=(
        "Alerts when the dead-letter folder contains files that failed processing."
    ),
    blocking=False,
)
def dead_letter_queue_check() -> AssetCheckResult:
    """Fail if any files exist in the dead-letter queue."""
    if not DEAD_LETTER_DIR.exists():
        return AssetCheckResult(
            passed=True,
            severity=AssetCheckSeverity.WARN,
            metadata={
                "dead_letter_path": MetadataValue.path(str(DEAD_LETTER_DIR)),
                "dead_letter_count": MetadataValue.text("0"),
            },
        )

    dead_files = [
        path for path in DEAD_LETTER_DIR.rglob("*")
        if path.is_file()
    ]
    if not dead_files:
        return AssetCheckResult(
            passed=True,
            severity=AssetCheckSeverity.WARN,
            metadata={
                "dead_letter_path": MetadataValue.path(str(DEAD_LETTER_DIR)),
                "dead_letter_count": MetadataValue.text("0"),
            },
        )

    sample_files = "\n".join(
        str(path.relative_to(DEAD_LETTER_DIR))
        for path in sorted(dead_files)[:20]
    )

    return AssetCheckResult(
        passed=False,
        severity=AssetCheckSeverity.ERROR,
        metadata={
            "dead_letter_path": MetadataValue.path(str(DEAD_LETTER_DIR)),
            "dead_letter_count": MetadataValue.text(str(len(dead_files))),
            "dead_letter_sample": MetadataValue.text(sample_files),
        },
    )