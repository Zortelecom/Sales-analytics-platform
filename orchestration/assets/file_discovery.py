"""Asset for discovering source files"""

from datetime import datetime, timedelta
from pathlib import Path
import sys
import json
import pandas as pd
from ingestion.orchestrate.file_discovery import FileDiscovery as FileDiscoveryClass
from ingestion.config.settings import load_sources_config
from orchestration.utils.constants import STATE_FILE
from dagster import asset, MetadataValue, AssetExecutionContext, AssetIn

# Add ingestion to path for imports
sys.path.append(str(Path(__file__).parent.parent.parent / "ingestion"))

def load_processed_files():
    """Load set of already processed file paths"""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, IOError):
            return set()
    return set()


def save_processed_files(files: set):
    """Save processed file paths to persistent state"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(files), f, indent=2)


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
    })

    context.log.info(f"Discovered {len(df)} files")
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

    # Update processed files state
    if not to_process.empty:
        new_files = set(to_process["file_path"])
        updated_state = processed_files | new_files
        save_processed_files(updated_state)
        context.log.info(
            f"Updated state: {len(updated_state)} total processed files")

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
