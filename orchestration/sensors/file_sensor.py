"""Sensor to trigger pipeline on new files"""
from pathlib import Path
from dagster import sensor, RunRequest, SkipReason, DefaultSensorStatus
from orchestration.jobs.daily_pipeline import ingestion_only_job
from orchestration.assets.file_discovery import load_processed_files
from ingestion.orchestrate.file_discovery import FileDiscovery as FileDiscoveryClass
from ingestion.config.settings import load_sources_config


# NOTE: this module previously declared its own STATE_FILE pointing at
# data/.dagster_sensor_state.json, relative to the working directory, and
# never used it -- the real state lives in
# orchestration.utils.constants.STATE_FILE, which load_processed_files() reads.
# Two state files, one of them a decoy, is how a "why did it reprocess
# everything" afternoon starts.

# 30 minutes, not 5. The workbooks are edited by supervisors over the course
# of a day, not streamed -- checking twelve files on a synced drive every five
# minutes is churn, and each check logged EVERY discovered file at INFO, which
# buried real events in the daemon log.
#
# Set default_status=STOPPED while iterating locally: an automatic run every
# time you save a workbook makes it hard to tell your own runs apart.
@sensor(
    job=ingestion_only_job,
    name="new_file_sensor",
    minimum_interval_seconds=1800,
    default_status=DefaultSensorStatus.RUNNING,
)
def new_file_sensor(context):
    """Trigger when new files appear in any configured source directory."""
    processed = load_processed_files()
    config = load_sources_config()
    discovery = FileDiscoveryClass(config)
    all_files = discovery.discover_all()

    new_files = [
        fp for files in all_files.values() for fp in files
        if str(fp) not in processed
    ]

    if new_files:
        # A count plus a sample. Logging all twelve paths on every evaluation
        # made the daemon log unreadable.
        context.log.info(
            "%d new/changed file(s), e.g. %s",
            len(new_files),
            ", ".join(fp.name for fp in new_files[:3]),
        )
        return RunRequest(
            run_key=f"new_files_{max(fp.stat().st_mtime for fp in new_files):.0f}",
            tags={"trigger": "sensor", "source": "file_detection"},
        )

    return SkipReason("No new files detected")