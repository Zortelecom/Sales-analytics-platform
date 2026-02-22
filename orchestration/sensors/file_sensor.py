"""Sensor to trigger pipeline on new files"""
from pathlib import Path
import json
from datetime import datetime
from dagster import sensor, RunRequest, SkipReason
from orchestration.jobs.daily_pipeline import ingestion_only_job
from orchestration.utils.constants import SOURCE_PATHS


# State file to track last check
STATE_FILE = Path("data/.dagster_sensor_state.json")


def get_last_check_time():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("last_check")
    return None


def save_last_check_time():
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_check": datetime.now().isoformat()}, f)


@sensor(
    job=ingestion_only_job,
    name="new_file_sensor",
    minimum_interval_seconds=300,  # Check every 5 minutes
)
def new_file_sensor(context):
    """Trigger when new files appear in source directories"""

    new_files_found = False

    for source_type, source_path in SOURCE_PATHS.items():
        if not source_path.exists():
            continue

        excel_files = list(source_path.glob("*.xlsx"))

        for file_path in excel_files:
            # Check if file is newer than last check
            stat = file_path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime)

            last_check = get_last_check_time()
            if last_check:
                last_check_dt = datetime.fromisoformat(last_check)
                if mtime <= last_check_dt:
                    continue

            new_files_found = True
            context.log.info(f"New file detected: {file_path}")

    save_last_check_time()

    if new_files_found:
        return RunRequest(
            run_key=f"new_files_{datetime.now().isoformat()}",
            run_config={},
            tags={"trigger": "sensor", "source": "file_detection"}
        )

    return SkipReason("No new files detected")
