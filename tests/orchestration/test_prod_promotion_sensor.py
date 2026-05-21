from datetime import datetime, timedelta, timezone

import pytest
from dagster import RunRequest, SkipReason

from orchestration.sensors.prod_promotion_sensor import prod_promotion_sensor


class DummyLog:
    def __init__(self):
        self.messages = []

    def info(self, message, *args):
        self.messages.append(message % args if args else message)


class DummyMaterialization:
    def __init__(self, timestamp, metadata_entries=None, run_id="run-1"):
        self.timestamp = timestamp
        self.metadata_entries = metadata_entries or []
        self.run_id = run_id


class DummyInstance:
    def __init__(self, materialization=None):
        self._materialization = materialization

    def get_latest_materialization(self, asset_key=None):
        return self._materialization


class DummyContext:
    def __init__(self, instance):
        self.instance = instance
        self.log = DummyLog()
        self.cursor = None


def make_metadata_entry(label, value):
    class Entry:
        def __init__(self, label, value):
            self.label = label
            self.entry_data = type("Data", (), {"text": value})()

    return Entry(label, value)


def test_prod_promotion_sensor_skips_when_no_materialization():
    context = DummyContext(DummyInstance(materialization=None))

    result = prod_promotion_sensor(context)

    assert isinstance(result, SkipReason)
    assert "No serving_database materialization" in str(result)


def test_prod_promotion_sensor_skips_when_materialization_too_old():
    old_timestamp = datetime.now(timezone.utc) - timedelta(hours=25)
    materialization = DummyMaterialization(
        timestamp=old_timestamp,
        metadata_entries=[make_metadata_entry("environment", "dev")],
    )
    context = DummyContext(DummyInstance(materialization=materialization))

    result = prod_promotion_sensor(context)

    assert isinstance(result, SkipReason)
    assert "No recent dev serving_database materialization" in str(result)


def test_prod_promotion_sensor_skips_when_environment_is_not_dev():
    recent_timestamp = datetime.now(timezone.utc) - timedelta(hours=1)
    materialization = DummyMaterialization(
        timestamp=recent_timestamp,
        metadata_entries=[make_metadata_entry("environment", "prod")],
    )
    context = DummyContext(DummyInstance(materialization=materialization))

    result = prod_promotion_sensor(context)

    assert isinstance(result, SkipReason)
    assert "not from dev" in str(result)


def test_prod_promotion_sensor_yields_run_request_when_gate_passes():
    recent_timestamp = datetime.now(timezone.utc) - timedelta(hours=1)
    materialization = DummyMaterialization(
        timestamp=recent_timestamp,
        metadata_entries=[make_metadata_entry("environment", "dev")],
        run_id="promote-run-123",
    )
    context = DummyContext(DummyInstance(materialization=materialization))

    result = prod_promotion_sensor(context)

    assert isinstance(result, RunRequest)
    assert result.run_key.startswith("prod_promotion_")
    assert result.tags["gate"] == "prod_promotion"
    assert result.tags["environment"] == "prod"
