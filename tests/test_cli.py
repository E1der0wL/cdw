"""CLI acceptance checks across independent clients of the persistent server."""

import json
import subprocess
import sys

import pytest

from cdw.config import build_config
from cdw.domain.serialization import preset_to_dict
from helpers import make_preset, stop_server


def invoke(directory, *arguments):
    return subprocess.run(
        [sys.executable, "-m", "cdw.cli", "--data-dir", str(directory), *arguments],
        capture_output=True, encoding="utf-8", timeout=45,
    )


@pytest.fixture
def workspace(tmp_path):
    yield tmp_path
    stop_server(build_config(tmp_path))


def register(directory, *, memory=False):
    preset = make_preset(build_config(directory), memory=memory)
    preset_file = directory / "preset.json"
    preset_file.write_text(json.dumps(preset_to_dict(preset)), encoding="utf-8")
    outcome = invoke(directory, "register", str(preset_file))
    assert outcome.returncode == 0, outcome.stderr
    document = json.loads(outcome.stdout)
    assert document["id"] == preset.id
    assert document["version"] == 2
    assert document["environment"] == {}
    return preset


@pytest.mark.parametrize("memory", [False, True])
def test_cli_registration_collection_query_and_error(workspace, memory):
    preset = register(workspace, memory=memory)
    result = invoke(workspace, "collect", preset.id)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["inserted_rows"] == 5
    query = invoke(workspace, "query", preset.id, "--sql", "SELECT count(*) FROM source.host_inventory")
    assert query.returncode == 0, query.stderr
    assert json.loads(query.stdout)["rows"] == [[5]]
    assert set(json.loads(query.stdout)) == {"columns", "types", "rows", "truncated", "elapsed_seconds"}
    bad = invoke(workspace, "query", preset.id, "--sql", "DROP TABLE source.host_inventory")
    assert bad.returncode == 1
    assert json.loads(bad.stderr)["error"] == "invalid_query"
    history = invoke(workspace, "history")
    assert json.loads(history.stdout)[0]["state"] == "succeeded"


def test_cli_server_lifecycle_and_memory_lifetime(workspace):
    status = invoke(workspace, "server", "status")
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["running"] is False
    start = invoke(workspace, "server", "start")
    assert start.returncode == 0, start.stderr
    started = json.loads(start.stdout)
    assert started["running"] is True and isinstance(started["pid"], int)
    assert set(started) == {"running", "pid", "started_at", "data_directory", "active_runs"}
    assert json.loads(invoke(workspace, "server", "start").stdout)["pid"] == started["pid"]
    preset = register(workspace, memory=True)
    assert invoke(workspace, "collect", preset.id).returncode == 0
    stopped = invoke(workspace, "server", "stop")
    assert stopped.returncode == 0, stopped.stderr
    assert json.loads(stopped.stdout)["running"] is False
    assert json.loads(invoke(workspace, "server", "stop").stdout)["running"] is False
    # A regular command starts a fresh server; definitions survive, memory data does not.
    presets = invoke(workspace, "presets")
    assert json.loads(presets.stdout)[0]["id"] == preset.id
    query = invoke(workspace, "query", preset.id, "--sql", "SELECT * FROM source.host_inventory")
    assert query.returncode == 1
    assert json.loads(query.stderr)["error"] == "query_failed"


def test_cli_has_no_demo_command_and_error_does_not_start_server(tmp_path):
    outcome = invoke(tmp_path, "demo")
    assert outcome.returncode == 2
    assert not (tmp_path / ".runtime" / "server.json").exists()
