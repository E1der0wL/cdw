"""Upgrade real version-two profiles atomically without rewriting their history."""

import json
import os
import sqlite3
import sys

import pytest

from cdw.application.service import ApplicationService
from cdw.application.tasks import TaskService
from cdw.config import build_config
from cdw.domain.appearance import AppearanceSettings, ThemeMode
from cdw.domain.contracts import CdwError, CommandMode, ErrorCode, RunState
from cdw.domain.parsing import ParseSpec
from cdw.domain.serialization import preset_from_dict
from cdw.domain.tasks import TaskDefinition
from cdw.infrastructure.metadata_database import MetadataDatabase


# This historical fixture is deliberately independent of the current schema builder.
VERSION_TWO_SCHEMA = """
CREATE TABLE presets (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, storage_key TEXT NOT NULL,
    table_name TEXT NOT NULL, definition TEXT NOT NULL, UNIQUE(storage_key, table_name)
);
CREATE TABLE runs (
    id TEXT PRIMARY KEY, preset_id TEXT NOT NULL, preset_name TEXT NOT NULL,
    state TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    row_count INTEGER, elapsed_seconds REAL, error_code TEXT,
    details TEXT NOT NULL DEFAULT '', stderr TEXT NOT NULL DEFAULT '',
    storage_key TEXT NOT NULL, table_name TEXT NOT NULL, preset_snapshot TEXT NOT NULL
);
CREATE INDEX runs_started ON runs(started_at DESC);
CREATE TABLE app_settings (key TEXT PRIMARY KEY, definition TEXT NOT NULL);
CREATE TABLE workspace_tasks (
    id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE, definition TEXT NOT NULL
);
PRAGMA user_version=2;
"""
STARTED = "2026-11-10T01:02:03+00:00"
FINISHED = "2026-11-10T01:02:04+00:00"
BASELINE_KEY = "CDW_MIGRATION_BASELINE"
OVERRIDE_KEY = "CDW_MIGRATION_OVERRIDE"


def legacy_preset(identifier="preset-one"):
    return {
        "version": 1, "id": identifier, "name": identifier, "executable": sys.executable,
        "arguments": ["-c", "print('[]')"], "working_directory": None,
        "environment": {OVERRIDE_KEY: "preset override"}, "output_format": "json",
        "encoding": "utf-8", "csv_delimiter": ",", "storage": {"mode": "memory", "path": None},
        "table_name": identifier.replace("-", "_"), "write_mode": "replace",
        "execution": {"timeout_seconds": 5, "max_output_bytes": 4096},
    }


def legacy_task(identifier="task-one"):
    return {
        "version": 1, "id": identifier, "name": identifier, "executable": sys.executable,
        "arguments": ["-c", "print('task')", "{input}"], "working_directory": None,
        "environment": {OVERRIDE_KEY: "task override"}, "input_format": "json", "csv_delimiter": ",",
        "execution": {"timeout_seconds": 5, "max_output_bytes": 4096},
    }


def create_legacy_profile(config, *, presets=None, tasks=None):
    presets = [legacy_preset()] if presets is None else presets
    tasks = [legacy_task()] if tasks is None else tasks
    config.metadata_database.parent.mkdir(parents=True, exist_ok=True)
    appearance = AppearanceSettings(mode=ThemeMode.DARK, font_size=12.0, ui_scale=1.1)
    with sqlite3.connect(config.metadata_database) as connection:
        connection.executescript(VERSION_TWO_SCHEMA)
        for preset in presets:
            connection.execute("INSERT INTO presets VALUES (?,?,?,?,?)", (
                preset["id"], preset["name"], ":memory:", preset["table_name"], json.dumps(preset, indent=2)))
        for task in tasks:
            connection.execute("INSERT INTO workspace_tasks VALUES (?,?,?)", (task["id"], task["name"], json.dumps(task, indent=2)))
        connection.execute("INSERT INTO app_settings VALUES (?,?)", ("appearance", json.dumps(appearance.to_dict(), indent=3)))
        history = dict(presets[0], environment={})
        connection.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "legacy-run", presets[0]["id"], presets[0]["name"], "succeeded", STARTED, FINISHED,
            5, 0.125, None, "retained details", "retained stderr", ":memory:",
            presets[0]["table_name"], json.dumps(history, indent=4)))
    return appearance


def database_snapshot(path):
    with sqlite3.connect(path) as connection:
        schema = tuple(connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
        records = {table: tuple(connection.execute(f"SELECT * FROM {table} ORDER BY rowid"))
                   for table in ("presets", "workspace_tasks", "runs", "app_settings")}
        return connection.execute("PRAGMA user_version").fetchone()[0], schema, records


def test_version_two_profile_upgrades_definitions_and_preserves_appearance_and_history(tmp_path, monkeypatch):
    config = build_config(tmp_path)
    expected_appearance = create_legacy_profile(config)
    _, _, original = database_snapshot(config.metadata_database)
    monkeypatch.setenv(BASELINE_KEY, "first host snapshot")
    monkeypatch.setenv(OVERRIDE_KEY, "host value")
    captured_environment = dict(os.environ)

    service = ApplicationService(config)
    try:
        preset = service.get_preset("preset-one")
        task = TaskService(config, database=service.database).get_task("task-one")
        assert dict(preset.environment) == {**captured_environment, OVERRIDE_KEY: "preset override"}
        assert dict(task.environment) == {**captured_environment, OVERRIDE_KEY: "task override"}
        assert preset.command_mode is task.command_mode is CommandMode.DIRECT
        assert preset.shell_script == task.script == ""
        assert preset.parsing == ParseSpec()
        assert service.load_appearance() == expected_appearance
        history = service.list_runs()
        assert len(history) == 1
        assert (history[0].id, history[0].state, history[0].row_count) == ("legacy-run", RunState.SUCCEEDED, 5)
        assert history[0].started_at == STARTED and history[0].finished_at == FINISHED
        assert history[0].details == "retained details" and history[0].stderr == "retained stderr"
        assert not config.default_database.exists()
    finally:
        service.close()

    version, schema, upgraded = database_snapshot(config.metadata_database)
    assert version == 3
    assert {"workflows", "schedules", "workflow_runs"} <= {row[1] for row in schema if row[0] == "table"}
    assert upgraded["runs"] == original["runs"]
    assert upgraded["app_settings"] == original["app_settings"]
    assert json.loads(upgraded["presets"][0][-1])["version"] == 2
    assert json.loads(upgraded["workspace_tasks"][0][-1])["version"] == 2

    # Reopening never overlays a changed host environment on the frozen snapshot.
    monkeypatch.setenv(BASELINE_KEY, "later host value")
    reopened = ApplicationService(config)
    try:
        assert reopened.get_preset("preset-one").environment[BASELINE_KEY] == "first host snapshot"
        assert TaskService(config, database=reopened.database).get_task("task-one").environment[BASELINE_KEY] == "first host snapshot"
    finally:
        reopened.close()


@pytest.mark.parametrize("invalid_target", ["preset", "task", "malformed_json", "non_mapping_json"])
def test_failed_upgrade_rolls_back_schema_and_every_source_document(tmp_path, invalid_target):
    config = build_config(tmp_path)
    presets = [legacy_preset("preset-one"), legacy_preset("preset-two")]
    tasks = [legacy_task("task-one"), legacy_task("task-two")]
    if invalid_target == "preset":
        presets[1]["executable"] = ""
    elif invalid_target == "task":
        tasks[1]["arguments"] = []
    create_legacy_profile(config, presets=presets, tasks=tasks)
    if invalid_target in ("malformed_json", "non_mapping_json"):
        malformed = "{invalid" if invalid_target == "malformed_json" else "[]"
        with sqlite3.connect(config.metadata_database) as connection:
            connection.execute("UPDATE workspace_tasks SET definition=? WHERE id='task-two'", (malformed,))
    before = database_snapshot(config.metadata_database)

    with pytest.raises(CdwError) as failure:
        MetadataDatabase(config.metadata_database)

    assert failure.value.code is ErrorCode.STORAGE_FAILED
    assert database_snapshot(config.metadata_database) == before


def test_current_documents_in_old_schema_keep_their_exact_environment_instead_of_being_migrated_twice(tmp_path, monkeypatch):
    config = build_config(tmp_path)
    create_legacy_profile(config)
    preset = legacy_preset()
    preset.update(version=2, command_mode="direct", shell_script="", parsing=ParseSpec().to_dict(), environment={"ONLY_SAVED": "preset"})
    task = legacy_task()
    task.update(version=2, command_mode="direct", script="", environment={"ONLY_SAVED": "task"})
    with sqlite3.connect(config.metadata_database) as connection:
        connection.execute("UPDATE presets SET definition=?", (json.dumps(preset),))
        connection.execute("UPDATE workspace_tasks SET definition=?", (json.dumps(task),))
    monkeypatch.setenv(BASELINE_KEY, "must not be added")
    MetadataDatabase(config.metadata_database)
    _, _, upgraded = database_snapshot(config.metadata_database)
    assert dict(preset_from_dict(json.loads(upgraded["presets"][0][-1])).environment) == {"ONLY_SAVED": "preset"}
    assert dict(TaskDefinition.from_dict(json.loads(upgraded["workspace_tasks"][0][-1])).environment) == {"ONLY_SAVED": "task"}


def test_future_metadata_schema_is_rejected_without_any_changes(tmp_path):
    config = build_config(tmp_path)
    create_legacy_profile(config)
    with sqlite3.connect(config.metadata_database) as connection:
        connection.execute("PRAGMA user_version=99")
    before = database_snapshot(config.metadata_database)
    with pytest.raises(CdwError) as failure:
        MetadataDatabase(config.metadata_database)
    assert failure.value.code is ErrorCode.STORAGE_FAILED
    assert database_snapshot(config.metadata_database) == before
