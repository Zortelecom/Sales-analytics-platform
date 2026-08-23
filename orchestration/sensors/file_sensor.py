"""
orchestration/sensors/file_sensor.py

Trigger the pipeline when a workbook is added OR edited.

(2026-08) TWO FIXES, BOTH ARCHITECTURE-DRIVEN.

1. IT COULD NOT SEE AN EDITED WORKBOOK
   ────────────────────────────────────
   It compared discovered paths against load_processed_files(), which is a
   back-compat shim over load_processed_state() that DISCARDS the hashes and
   returns paths only:

       new_files = [fp for ... if str(fp) not in processed]

   So a supervisor editing ExSD-Sales-Centre.xlsx -- the normal daily case,
   and the exact scenario the hash state was introduced for -- left the path
   in the set and the sensor skipped. The docstring and the log line both
   claimed "new/changed"; only "new" was true.

   files_to_process already had the right predicate. It is now shared rather
   than reimplemented, so the sensor and the asset cannot disagree about what
   "changed" means.

2. IT TRIGGERED ingestion_only_job, WHICH NOW STOPS SHORT
   ───────────────────────────────────────────────────────
   Under the seed architecture, ending at the seed write was harmless: seeds
   were inert until SQLMesh ran. Under the lake-native architecture the same
   stopping point leaves rows IN the landing schema with no raw.*, staging.*,
   marts.* or bi.* reflecting them -- reporting is unchanged while the lake
   says data arrived.

   That is precisely the state meta.freshness exists to flag ("arrived on
   time, contained nothing new"), reached deliberately by our own sensor.

   It now runs the full pipeline.

INTERVAL
────────
30 minutes. The workbooks are edited by supervisors over the course of a day,
not streamed. Note this now hashes every discovered file per evaluation --
around a dozen files on a synced drive, which is cheap, but if the corpus
grows to hundreds, gate on mtime first and hash only the candidates.

Set default_status=STOPPED while iterating locally: an automatic run every
time you save a workbook makes it hard to tell your own runs apart.
"""
from pathlib import Path

from dagster import DefaultSensorStatus, RunRequest, SkipReason, sensor

from ingestion.config.settings import load_sources_config
from ingestion.orchestrate.file_discovery import FileDiscovery as FileDiscoveryClass
from orchestration.assets.file_discovery import file_sha256, load_processed_state
from orchestration.jobs.daily_pipeline import daily_pipeline_job

# NOTE: this module previously declared its own STATE_FILE pointing at
# data/.dagster_sensor_state.json, relative to the working directory, and
# never used it -- the real state lives in
# orchestration.utils.constants.STATE_FILE, which the loaders below read.
# Two state files, one of them a decoy, is how a "why did it reprocess
# everything" afternoon starts.


def _is_changed(path: Path, state: dict) -> bool:
    """
    Same rule as files_to_process: unknown path, or content differing from the
    recorded sha256. An unreadable file counts as changed -- the safe
    direction, since the alternative is silently never processing it.
    """
    recorded = state.get(str(path))
    if not recorded:
        return True
    try:
        return file_sha256(path) != recorded
    except OSError:
        return True


@sensor(
    job=daily_pipeline_job,
    name="new_file_sensor",
    minimum_interval_seconds=1800,
    default_status=DefaultSensorStatus.RUNNING,
    description=(
        "Runs the full pipeline when a source workbook is added or edited. "
        "Content-hashed, so an edit to an existing file is detected."
    ),
)
def new_file_sensor(context):
    """Trigger when any configured source directory has new or changed content."""
    state = load_processed_state()
    discovery = FileDiscoveryClass(load_sources_config())
    all_files = discovery.discover_all()

    changed = [
        fp
        for files in all_files.values()
        for fp in files
        if _is_changed(fp, state)
    ]

    if not changed:
        return SkipReason("No new or changed files detected")

    # A count plus a sample. Logging all twelve paths on every evaluation made
    # the daemon log unreadable.
    context.log.info(
        "%d new/changed file(s), e.g. %s",
        len(changed),
        ", ".join(fp.name for fp in changed[:3]),
    )

    # Keyed on the newest mtime across the changed set: repeated evaluations
    # over the same unprocessed edit collapse to one run, while a further edit
    # produces a new key.
    return RunRequest(
        run_key=f"new_files_{max(fp.stat().st_mtime for fp in changed):.0f}",
        tags={"trigger": "sensor", "source": "file_detection"},
    )