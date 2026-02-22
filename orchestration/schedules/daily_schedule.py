"""Schedule definitions"""
from dagster import ScheduleDefinition, DefaultScheduleStatus
from orchestration.jobs.daily_pipeline import daily_pipeline_job

# Run daily at 6:00 AM
daily_6am_schedule = ScheduleDefinition(
    job=daily_pipeline_job,
    cron_schedule="0 6 * * *",  # 6 AM daily
    name="daily_6am_schedule",
    description="Run full pipeline every morning at 6 AM",
    default_status=DefaultScheduleStatus.RUNNING,
    execution_timezone="Africa/Douala",  # Adjust to your timezone
)

# Optional: Mid-day refresh for sales data
midday_schedule = ScheduleDefinition(
    job=daily_pipeline_job,
    cron_schedule="0 12 * * 1-5",  # 12 PM on weekdays only
    name="midday_refresh",
    description="Mid-day refresh for intraday sales data",
    default_status=DefaultScheduleStatus.STOPPED,  # Disabled by default
)
