"""Asset for discovering source files"""

from datetime import datetime, timedelta
from pathlib import Path
import sys
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

def load_processed_files():
    """Load set of already processed file paths"""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                if isinstance(state, dict):
                    return set(state.get("processed_files", []))
                return set(state)
        except (json.JSONDecodeError, IOError):
            return set()
    return set()


def save_processed_files(files: set):
    """Save processed file paths to persistent state atomically."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=str(STATE_FILE.parent),
        suffix=".tmp",
    ) as temp_file:
        json.dump(list(files), temp_file, indent=2)
        temp_path = Path(temp_file.name)

    temp_path.replace(STATE_FILE)


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
        "processed_files_in_state": len(processed_files),
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
    processed_files = load_processed_files()

    if processed_files:
        # Filter out already processed files
        to_process = discovered_files[
            ~discovered_files["file_path"].isin(processed_files)
        ].copy()

        context.log.info(
            f"State-based filtering: {len(to_process)} new files "
            f"({len(processed_files)} already processed)"
        )
    else:
        # No state exists - use 24-hour lookback as fallback
        cutoff_time = datetime.now() - timedelta(hours=24)
        to_process = discovered_files[
            discovered_files["modified_time"] > cutoff_time
        ].copy()

        context.log.warning(
            f"No state file found, using 24h lookback: {len(to_process)} files"
        )

    context.add_output_metadata({
        "row_count": len(to_process),
        "new_files": len(to_process),
        "previously_processed": len(processed_files),
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
