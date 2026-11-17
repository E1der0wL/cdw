"""Public local-server behavior across client closure and real process restart."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from cdw.config import build_config
from cdw.domain.contracts import (
    CdwError, ErrorCode, ExecutionPolicy, OutputFormat, Preset, RunState, StorageMode, StorageTarget, WriteMode,
)
from cdw.domain.schedules import ScheduleDefinition, TriggerKind, TriggerSpec, WorkflowDefinition, WorkflowRunState
from cdw.domain.tasks import TaskDefinition, TaskInputFormat
from cdw.server.client import RemoteApplicationService, ServerClient


WAIT_SECONDS = 15
POLL_SECONDS = 0.05
COMMAND_POLICY = ExecutionPolicy(5, 65536)
COLLECT_PROGRAM = "print('name,value'); print('alpha,3'); print('beta,5')"
COPY_INPUT_PROGRAM = (
    "from pathlib import Path; import sys; "
    "Path(sys.argv[2]).write_text(Path(sys.argv[1]).read_text(encoding='utf-8'), encoding='utf-8'); "
    "print('processed')"
)
GATED_COLLECT_PROGRAM = """
from pathlib import Path
import sys
import time
started, release = (Path(value) for value in sys.argv[1:])
started.write_text('running', encoding='utf-8')
deadline = time.monotonic() + 12
while not release.exists():
    if time.monotonic() >= deadline:
        raise TimeoutError('test command was not released')
    time.sleep(0.02)
print('name,value')
print('alpha,3')
print('beta,5')
"""
RECORD_ARGUMENTS_PROGRAM = (
    "from pathlib import Path; import json, sys; "
    "Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]), encoding='utf-8'); print('executed')"
)
FAILING_TASK_PROGRAM = (
    "import json, sys; "
    "document = json.load(open(sys.argv[1], encoding='utf-8')); "
    "assert document['rows'] == [['alpha', 3], ['beta', 5]]; "
    "print('task failure diagnostic', file=sys.stderr); sys.exit(13)"
)


def wait_until(predicate, *, timeout=WAIT_SECONDS):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(POLL_SECONDS)
    raise AssertionError("Timed out waiting for the local server")


def preset_for(directory, *, file=False):
    storage = StorageTarget(StorageMode.FILE, str(directory / "collected.duckdb")) if file else StorageTarget(StorageMode.MEMORY, None)
    return Preset("preset", "Actual command", sys.executable, ("-c", COLLECT_PROGRAM),
                  str(directory), dict(os.environ), OutputFormat.CSV, "utf-8", ",", storage,
                  "collected", WriteMode.REPLACE, COMMAND_POLICY)


def gated_preset(directory):
    started, release = directory / "command-started", directory / "command-release"
    preset = replace(preset_for(directory, file=True),
        arguments=("-c", GATED_COLLECT_PROGRAM, str(started), str(release)),
        execution=ExecutionPolicy(WAIT_SECONDS, COMMAND_POLICY.max_output_bytes))
    return preset, started, release


def finished_workflow(service, run_id):
    run = service.schedules.get_run(run_id)
    return run if run.finished_at is not None else None


@pytest.fixture
def remote(tmp_path, monkeypatch):
    """Use the public launcher, retaining only child handles for bounded cleanup."""
    processes = []
    original_popen = subprocess.Popen

    def remember_process(arguments, *args, **kwargs):
        process = original_popen(arguments, *args, **kwargs)
        if isinstance(arguments, (tuple, list)) and "cdw.server" in arguments:
            processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", remember_process)
    config = build_config(tmp_path / "profile")
    service = None
    try:
        service = RemoteApplicationService(config)
        yield service, processes
    finally:
        if service is not None:
            service.close()
        client = ServerClient(config)
        if client.status()["running"]:
            try:
                client.call("stop")
            except CdwError:
                pass
        clean_shutdown = True
        for process in processes:
            try:
                process.wait(timeout=WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                clean_shutdown = False
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        assert clean_shutdown, "The owned test server did not complete explicit shutdown"


def read_rows(service, preset):
    snapshot = service.query_workspace(preset.id, f"SELECT * FROM {preset.qualified_table} ORDER BY name")
    try:
        return snapshot.result.rows
    finally:
        service.release_result(snapshot.id)


def test_closing_client_keeps_detached_server_and_memory_tables(remote, tmp_path):
    service, processes = remote
    preset = preset_for(tmp_path)
    service.save_preset(preset)
    assert service.collect(preset.id).total_rows == 2
    original_pid = service.schedules.server_status()["pid"]
    assert original_pid != os.getpid()
    service.close()
    reconnected = RemoteApplicationService(service.config, start=False)
    try:
        assert reconnected.schedules.server_status()["pid"] == original_pid
        assert read_rows(reconnected, preset) == (("alpha", 3), ("beta", 5))
        assert len(processes) == 1 and processes[0].poll() is None
    finally:
        reconnected.close()


def test_disconnecting_an_ongoing_command_keeps_execution_and_durable_log(remote, tmp_path):
    service, processes = remote
    preset, started, release = gated_preset(tmp_path)
    service.save_preset(preset)
    client_outcomes = []

    def collect_from_gui_connection():
        try:
            client_outcomes.append(service.client.run("collect", preset.id))
        except Exception as error:
            client_outcomes.append(error)

    worker = threading.Thread(target=collect_from_gui_connection, daemon=True)
    worker.start()
    observer = None
    try:
        wait_until(started.exists)
        service.client.close()
        worker.join(timeout=WAIT_SECONDS)
        assert not worker.is_alive()
        assert len(client_outcomes) == 1
        assert isinstance(client_outcomes[0], CdwError)
        assert client_outcomes[0].code is ErrorCode.CANCELLED
        observer = RemoteApplicationService(service.config, start=False)
        assert observer.schedules.server_status()["active_runs"] == 1
        assert observer.last_success(preset.id) is None
        release.write_text("continue", encoding="utf-8")
        record = wait_until(lambda: observer.last_success(preset.id))
        assert record.state is RunState.SUCCEEDED and record.row_count == 2
        assert record.finished_at is not None and record.error_code is None
        assert read_rows(observer, preset) == (("alpha", 3), ("beta", 5))
        observer.schedules.stop_server()
        processes[-1].wait(timeout=WAIT_SECONDS)
        observer.close()
        observer = RemoteApplicationService(service.config)
        assert observer.last_success(preset.id) == record
        assert read_rows(observer, preset) == (("alpha", 3), ("beta", 5))
    finally:
        release.write_text("continue", encoding="utf-8")
        service.client.close()
        worker.join(timeout=WAIT_SECONDS)
        if observer is not None:
            observer.close()


def test_scheduled_collection_and_external_protocol_work_after_client_closes(remote, tmp_path):
    service, _ = remote
    preset = preset_for(tmp_path)
    service.save_preset(preset)
    received = tmp_path / "received.json"
    task = TaskDefinition("task", "Receive the table", sys.executable,
        ("-c", COPY_INPUT_PROGRAM, "{input}", str(received)), str(tmp_path), dict(os.environ),
        TaskInputFormat.JSON, ",", COMMAND_POLICY)
    service.tasks.save_task(task)
    workflow = WorkflowDefinition("workflow", "Collect and send", preset.id, task.id)
    trigger = TriggerSpec(TriggerKind.ONCE, at=(datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat())
    schedule = ScheduleDefinition("schedule", "Send once", workflow.id, trigger)
    service.schedules.save_bundle(workflow, schedule)
    service.close()
    reconnected = RemoteApplicationService(service.config, start=False)
    try:
        def completed_run():
            runs = reconnected.schedules.list_runs(schedule_id=schedule.id)
            return runs[0] if runs and runs[0].finished_at is not None else None
        run = wait_until(completed_run)
        assert run.state is WorkflowRunState.SUCCEEDED, run.details
        assert run.collection_run_id is not None
        assert run.stdout.strip() == "processed"
        document = json.loads(received.read_text(encoding="utf-8"))
        assert document["protocol"] == "cdw.table" and document["version"] == 1
        assert document["run_id"] == run.id and document["table_name"] == preset.table_name
        assert document["columns"] == ["name", "value"]
        assert document["types"] == ["string", "integer"]
        assert document["rows"] == [["alpha", 3], ["beta", 5]]
        assert reconnected.schedules.get_schedule(schedule.id).next_run_at is None
        assert read_rows(reconnected, preset) == (("alpha", 3), ("beta", 5))
    finally:
        reconnected.close()


def test_collection_failure_prevents_external_task_execution(remote, tmp_path):
    service, _ = remote
    preset = replace(preset_for(tmp_path), arguments=("-c", "import sys; print('failed', file=sys.stderr); sys.exit(7)"))
    service.save_preset(preset)
    marker = tmp_path / "should-not-exist.txt"
    task = TaskDefinition("task", "Must not run", sys.executable,
        ("-c", "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('unexpected')", str(marker)),
        str(tmp_path), dict(os.environ), TaskInputFormat.NONE, ",", COMMAND_POLICY)
    service.tasks.save_task(task)
    workflow = WorkflowDefinition("workflow", "Fail before task", preset.id, task.id)
    service.schedules.save_workflow(workflow)
    run_id = service.schedules.run_workflow(workflow.id)
    run = wait_until(lambda: (value if (value := service.schedules.get_run(run_id)).finished_at else None))
    assert run.state is WorkflowRunState.COLLECTION_FAILED
    assert run.collection_run_id is None
    assert not marker.exists()


def test_editing_saved_preset_during_workflow_preserves_captured_collection_and_snapshot(remote, tmp_path):
    service, _ = remote
    preset, started, release = gated_preset(tmp_path)
    service.save_preset(preset)
    received = tmp_path / "captured-table.json"
    task = TaskDefinition("task", "Receive captured table", sys.executable,
        ("-c", COPY_INPUT_PROGRAM, "{input}", str(received)), str(tmp_path), dict(os.environ),
        TaskInputFormat.JSON, ",", COMMAND_POLICY)
    service.tasks.save_task(task)
    workflow = WorkflowDefinition("workflow", "Use captured definition", preset.id, task.id)
    service.schedules.save_workflow(workflow)
    run_id = service.schedules.run_workflow(workflow.id)
    try:
        wait_until(started.exists)
        edited = replace(preset, name="Edited while running", table_name="edited_table",
            storage=StorageTarget(StorageMode.FILE, str(tmp_path / "edited.duckdb")),
            arguments=("-c", "print('name,value'); print('replacement,99')"))
        service.save_preset(edited)
        release.write_text("continue", encoding="utf-8")
        run = wait_until(lambda: finished_workflow(service, run_id))
        assert run.state is WorkflowRunState.SUCCEEDED, run.details
        assert service.get_preset(preset.id) == edited
        document = json.loads(received.read_text(encoding="utf-8"))
        assert document["table_name"] == preset.table_name
        assert document["rows"] == [["alpha", 3], ["beta", 5]]
        collection = next(record for record in service.list_runs() if record.id == run.collection_run_id)
        assert (collection.table_name, collection.storage_key, collection.preset_name) == (preset.table_name, preset.storage.key, preset.name)
        assert document["collected_at"] == collection.finished_at
        assert Path(preset.storage.path).exists()
        assert not Path(edited.storage.path).exists()
    finally:
        release.write_text("continue", encoding="utf-8")


def test_none_input_task_runs_without_tables_and_as_a_collection_followup(remote, tmp_path):
    service, _ = remote
    arguments_file = tmp_path / "received-arguments.json"
    task = TaskDefinition("task", "Run without table protocol", sys.executable,
        ("-c", RECORD_ARGUMENTS_PROGRAM, str(arguments_file), "literal-argument"),
        str(tmp_path), dict(os.environ), TaskInputFormat.NONE, ",", COMMAND_POLICY)
    service.tasks.save_task(task)
    assert service.workspace_tables() == []
    outcome = service.tasks.run_task(task.id)
    assert outcome.stdout.strip() == "executed"
    assert json.loads(arguments_file.read_text(encoding="utf-8")) == ["literal-argument"]
    arguments_file.unlink()
    preset = preset_for(tmp_path)
    service.save_preset(preset)
    workflow = WorkflowDefinition("workflow", "Collect then launch", preset.id, task.id)
    service.schedules.save_workflow(workflow)
    run_id = service.schedules.run_workflow(workflow.id)
    run = wait_until(lambda: finished_workflow(service, run_id))
    assert run.state is WorkflowRunState.SUCCEEDED, run.details
    assert run.collection_run_id is not None
    assert json.loads(arguments_file.read_text(encoding="utf-8")) == ["literal-argument"]
    assert read_rows(service, preset) == (("alpha", 3), ("beta", 5))


def test_external_task_failure_keeps_successfully_committed_collection(remote, tmp_path):
    service, _ = remote
    preset = preset_for(tmp_path, file=True)
    service.save_preset(preset)
    task = TaskDefinition("task", "Fail after receiving table", sys.executable,
        ("-c", FAILING_TASK_PROGRAM, "{input}"), str(tmp_path), dict(os.environ),
        TaskInputFormat.JSON, ",", COMMAND_POLICY)
    service.tasks.save_task(task)
    workflow = WorkflowDefinition("workflow", "Committed collection before task", preset.id, task.id)
    service.schedules.save_workflow(workflow)
    run_id = service.schedules.run_workflow(workflow.id)
    run = wait_until(lambda: finished_workflow(service, run_id))
    assert run.state is WorkflowRunState.TASK_FAILED
    assert "task failure diagnostic" in run.details
    collection = service.last_success(preset.id)
    assert collection is not None and collection.id == run.collection_run_id
    assert collection.state is RunState.SUCCEEDED and collection.row_count == 2
    assert read_rows(service, preset) == (("alpha", 3), ("beta", 5))


def test_file_tables_and_schedule_definitions_survive_server_restart(remote, tmp_path):
    service, processes = remote
    preset = preset_for(tmp_path, file=True)
    service.save_preset(preset)
    service.collect(preset.id)
    workflow = WorkflowDefinition("workflow", "File collection", preset.id)
    trigger = TriggerSpec(TriggerKind.INTERVAL, interval_seconds=60,
                          anchor_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    schedule = ScheduleDefinition("schedule", "Persisted schedule", workflow.id, trigger, enabled=False)
    service.schedules.save_bundle(workflow, schedule)
    old_pid = service.schedules.server_status()["pid"]
    service.schedules.stop_server()
    processes[-1].wait(timeout=WAIT_SECONDS)
    service.close()
    restarted = RemoteApplicationService(service.config)
    try:
        assert restarted.schedules.server_status()["pid"] != old_pid
        assert restarted.get_preset(preset.id) == preset
        assert restarted.schedules.get_schedule(schedule.id).definition == schedule
        assert restarted.schedules.get_workflow(workflow.id) == workflow
        assert read_rows(restarted, preset) == (("alpha", 3), ("beta", 5))
        assert len(processes) == 2
    finally:
        restarted.close()


def test_headless_server_imports_no_qt_modules():
    program = """
import json
import sys
import cdw.server.__main__
import cdw.server.runtime
import cdw.server.client
print(json.dumps([name for name in sys.modules if name == 'qtpy' or name.startswith(('qtpy.', 'PySide6'))]))
"""
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True,
                            timeout=WAIT_SECONDS, env=dict(os.environ, QT_API="not-a-qt-binding"))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []
