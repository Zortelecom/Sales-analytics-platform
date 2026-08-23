"""
orchestration/assets/file_discovery.py

Discovery and change-detection for source workbooks.

(2026-08) THREE CHANGES.

1. THE METADATA COUNTED THREE SOURCE TYPES OUT OF FOUR
   ────────────────────────────────────────────────────
   sales_files / targets_files / references_files were hardcoded. kp_sd -- the
   entire SELL-IN side of the distribution chain, and half of what makes
   sell-in vs sell-out reconciliation possible -- did not appear in the asset's
   UI summary at all. The counts are derived from the frame now, so a fifth
   source type cannot be forgotten the same way.

2. QUARANTINE IS TRACKED BY CONTENT HASH
   ──────────────────────────────────────
   preprocessing used to shutil.move a failing workbook out of the source
   directory. That directory is the supervisors' SYNCED folder, so the move
   propagated as a DELETE to everyone's copy -- a supervisor's file vanishing
   because an extractor disliked a sheet name.

   Copying instead means the file stays discoverable and would fail again
   every run. load_quarantine / record_quarantine break that loop: a file is
   skipped while its content is unchanged, and retried the moment a supervisor
   edits it. Same mechanism as the processed-state hashing, same reason.

3. dead_letter_queue_check NO LONGER LATCHES ON ERROR FOREVER
   ───────────────────────────────────────────────────────────
   It returned passed=False for any file under data/dead_letter/, and nothing
   ever empties that directory. So the first bad workbook turned the check red
   permanently, which is indistinguishable from a check nobody has looked at.
   Recent failures are ERROR; older ones are WARN and reported as history.
"""

import hashlib
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from dagster import (
    AssetCheckResult,
    AssetCheckSeverity,
    AssetExecutionContext,
    AssetIn,
    MetadataValue,
    asset,
    asset_check,
)

from ingestion.config.settings import load_sources_config
from ingestion.orchestrate.file_discovery import FileDiscovery as FileDiscoveryClass
from orchestration.utils.constants import DEAD_LETTER_DIR, STATE_FILE

# Failures older than this are history, not an open incident.
DEAD_LETTER_FRESH_DAYS = 7

# Quarantine manifest: {source_path: sha256} for workbooks that failed
# preprocessing. Lives beside the dead-letter copies rather than in
# STATE_FILE, so the processed-state schema stays a flat {path: hash} map.
QUARANTINE_FILE = DEAD_LETTER_DIR / "_quarantine.json"


def file_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
    """Content hash of a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict) -> None:
    """Write via a temp file in the same directory, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=str(path.parent), suffix=".tmp",
    ) as temp_file:
        json.dump(payload, temp_file, indent=2)
        temp_path = Path(temp_file.name)
    temp_path.replace(path)


def _read_json_dict(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}
    return data if isinstance(data, dict) else {}


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
    state = _read_json_dict(STATE_FILE)
    if "processed_files" in state:
        state = state["processed_files"]
    # Migrate the old list/set form: unknown hashes force one re-process,
    # which is the safe direction.
    if isinstance(state, list):
        return {path: "" for path in state}
    return state if isinstance(state, dict) else {}


def save_processed_state(state: dict) -> None:
    """Persist {source_path: sha256} atomically."""
    _write_json_atomic(STATE_FILE, state)


def load_processed_files() -> set:
    """
    Back-compat: just the paths.

    DO NOT use this for change detection -- discarding the hashes is what made
    new_file_sensor blind to an EDITED workbook. Use load_processed_state().
    """
    return set(load_processed_state())


def save_processed_files(files: set) -> None:
    """Back-compat shim; records an unknown hash so the file is re-checked."""
    state = load_processed_state()
    state.update({str(path): state.get(str(path), "") for path in files})
    save_processed_state(state)


def load_quarantine() -> dict:
    """{source_path: sha256} for workbooks that failed preprocessing."""
    return _read_json_dict(QUARANTINE_FILE)


def record_quarantine(source_path: Path, content_hash: str) -> None:
    """Mark a workbook as failed at this exact content."""
    quarantine = load_quarantine()
    quarantine[str(source_path)] = content_hash
    _write_json_atomic(QUARANTINE_FILE, quarantine)


def clear_quarantine(source_path: Path) -> None:
    """Forget a workbook, so a changed version is retried."""
    quarantine = load_quarantine()
    if quarantine.pop(str(source_path), None) is not None:
        _write_json_atomic(QUARANTINE_FILE, quarantine)


def is_quarantined(source_path: Path, content_hash: str) -> bool:
    """
    True when this file failed before AND has not changed since.

    An empty hash (unreadable file) is never treated as quarantined: better to
    retry and fail loudly than to skip on a hash we could not compute.
    """
    if not content_hash:
        return False
    return load_quarantine().get(str(source_path)) == content_hash


@asset(
    group_name="ingestion",
    description="Discovers Excel workbooks in the configured source directories",
    compute_kind="python",
)
def discovered_files(context: AssetExecutionContext) -> pd.DataFrame:
    """
    Scan source directories and return a manifest of everything present.

    Columns: source_type, file_name, file_path, size_bytes, size_mb,
    modified_time, discovered_at.

    source_type is whatever sources.yaml declares -- today sales, targets,
    references and kp_sd. Nothing here enumerates them.
    """
    discovery = FileDiscoveryClass(load_sources_config())
    all_files = discovery.discover_all()

    files_data = [
        {
            "source_type": source_type,
            "file_name": file_path.name,
            "file_path": str(file_path),
            "size_bytes": file_path.stat().st_size,
            "size_mb": file_path.stat().st_size / (1024 * 1024),
            "modified_time": datetime.fromtimestamp(file_path.stat().st_mtime),
            "discovered_at": datetime.now(),
        }
        for source_type, files in all_files.items()
        for file_path in files
    ]

    df = pd.DataFrame(files_data)
    state = load_processed_state()
    quarantined = load_quarantine()

    # Derived, not hardcoded. The previous version listed sales/targets/
    # references and silently omitted kp_sd -- the whole sell-in tier.
    by_type = (
        df["source_type"].value_counts().to_dict() if not df.empty else {}
    )

    context.add_output_metadata({
        "row_count": len(df),
        "files_by_source_type": MetadataValue.json(by_type),
        "total_size_mb": f"{df['size_mb'].sum():.2f}" if not df.empty else "0",
        "state_file": MetadataValue.path(str(STATE_FILE)),
        "files_in_state": len(state),
        "files_quarantined": len(quarantined),
        "preview": MetadataValue.md(
            df.head(10).to_markdown() if not df.empty else "No files found"
        ),
    })

    context.log.info(
        "Discovered %d file(s) across %d source type(s); %d in state, %d quarantined",
        len(df), len(by_type), len(state), len(quarantined),
    )
    if not df.empty:
        for source_type, count in sorted(by_type.items()):
            context.log.info("  %-12s %d file(s)", source_type, count)

    return df


@asset(
    group_name="ingestion",
    description="Filters to workbooks whose content changed since the last run",
    ins={"discovered_files": AssetIn()},
)
def files_to_process(
    context: AssetExecutionContext, discovered_files: pd.DataFrame
) -> pd.DataFrame:
    """
    Keep only files that are new or whose bytes changed.

    Falls back to a 24-hour mtime lookback ONLY on a cold start with no state
    file. That fallback is why `python -m ingestion.main --all` exists for a
    backfill: a 24-hour window is right for a scheduled run and wrong for a
    first load.
    """
    if discovered_files.empty:
        context.log.info("No files discovered")
        return pd.DataFrame()

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

        to_process = discovered_files[discovered_files.apply(_is_new, axis=1)].copy()
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
            "No state file found, using 24h lookback: %d file(s). For a first "
            "load use `python -m ingestion.main --all`, which ignores mtime.",
            len(to_process),
        )

    context.add_output_metadata({
        "row_count": len(to_process),
        "previously_processed": len(state),
        "changed_by_source_type": MetadataValue.json(
            to_process["source_type"].value_counts().to_dict()
            if not to_process.empty else {}
        ),
        "preview": MetadataValue.md(
            to_process[["source_type", "file_name", "modified_time"]].to_markdown()
            if not to_process.empty else "No new files to process"
        ),
    })

    if to_process.empty:
        context.log.info("No new files to process")
    else:
        context.log.info("Files to process: %d", len(to_process))
        for _, row in to_process.iterrows():
            context.log.info("  - [%s] %s", row["source_type"], row["file_name"])

    return to_process


@asset_check(
    asset="discovered_files",
    name="dead_letter_queue_check",
    description=(
        "Fails when a workbook has failed preprocessing recently. Older "
        "failures are reported as history, not as an open incident."
    ),
    blocking=False,
)
def dead_letter_queue_check() -> AssetCheckResult:
    """
    Recent failures fail the check; old ones warn.

    The previous version returned passed=False for ANY file under
    data/dead_letter/, and nothing ever empties that directory -- so one bad
    workbook in March turned the check permanently red, which looks exactly
    like a check nobody reads. A permanently failing check is a disabled check
    with extra steps.
    """
    quarantined = load_quarantine()

    if not DEAD_LETTER_DIR.exists():
        return AssetCheckResult(
            passed=True,
            severity=AssetCheckSeverity.WARN,
            metadata={
                "dead_letter_path": MetadataValue.path(str(DEAD_LETTER_DIR)),
                "dead_letter_count": 0,
                "quarantined_files": 0,
            },
        )

    dead_files = [
        path for path in DEAD_LETTER_DIR.rglob("*")
        if path.is_file() and path != QUARANTINE_FILE
    ]

    cutoff = datetime.now() - timedelta(days=DEAD_LETTER_FRESH_DAYS)
    recent = [p for p in dead_files if datetime.fromtimestamp(p.stat().st_mtime) > cutoff]
    older = [p for p in dead_files if p not in recent]

    base_metadata = {
        "dead_letter_path": MetadataValue.path(str(DEAD_LETTER_DIR)),
        "dead_letter_count": len(dead_files),
        "recent_failures": len(recent),
        "older_failures": len(older),
        "quarantined_files": len(quarantined),
        "fresh_window_days": DEAD_LETTER_FRESH_DAYS,
    }

    if recent:
        base_metadata["recent_sample"] = MetadataValue.text(
            "\n".join(
                str(p.relative_to(DEAD_LETTER_DIR)) for p in sorted(recent)[:20]
            )
        )
        return AssetCheckResult(
            passed=False,
            severity=AssetCheckSeverity.ERROR,
            metadata=base_metadata,
        )

    if older:
        base_metadata["older_sample"] = MetadataValue.text(
            "\n".join(
                str(p.relative_to(DEAD_LETTER_DIR)) for p in sorted(older)[:20]
            )
        )

    return AssetCheckResult(
        passed=True,
        severity=AssetCheckSeverity.WARN,
        metadata=base_metadata,
    )