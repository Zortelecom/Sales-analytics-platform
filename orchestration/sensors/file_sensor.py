"""Sensor to trigger pipeline on new files"""
from pathlib import Path
from dagster import sensor, RunRequest, SkipReason, DefaultSensorStatus
from orchestration.jobs.daily_pipeline import ingestion_only_job
from orchestration.assets.file_discovery import load_processed_files
from ingestion.orchestrate.file_discovery import FileDiscovery as FileDiscoveryClass
from ingestion.config.settings import load_sources_config


# State file to track last check
STATE_FILE = Path("data/.dagster_sensor_state.json")

@sensor(
    job=ingestion_only_job,
    name="new_file_sensor",
    minimum_interval_seconds=300,
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
        for fp in new_files:
            context.log.info(f"New file detected: {fp}")
        return RunRequest(
            run_key=f"new_files_{max(fp.stat().st_mtime for fp in new_files):.0f}",
            tags={"trigger": "sensor", "source": "file_detection"},
        )

    return SkipReason("No new files detected")
