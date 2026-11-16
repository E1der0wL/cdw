"""Acceptance contracts through the public service, real processes and real DBs."""

import json
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from cdw.application.examples import demo_preset
from cdw.application.service import ApplicationService
from cdw.config import build_config
from cdw.domain.contracts import (
    CdwError, ErrorCode, ExecutionPolicy, OutputFormat, QueryPolicy, RunState,
    StorageMode, StorageTarget, WriteMode,
)
from cdw.domain.serialization import preset_from_dict, preset_to_dict
from cdw.infrastructure.profile_lock import ProfileLock


@pytest.fixture
def service(tmp_path):
    instance = ApplicationService(build_config(tmp_path))
    yield instance
    instance.close()


def command(service, program, **changes):
    preset = replace(demo_preset(service.config), arguments=("-c", program), **changes)
    return service.save_preset(preset)


def output(service, raw: str, **changes):
    return command(service, f"import sys; sys.stdout.buffer.write({raw.encode('utf-8')!r})", **changes)


@pytest.mark.parametrize("memory", [False, True])
def test_register_collect_query_and_restart(tmp_path, memory):
    config = build_config(tmp_path)
    first = ApplicationService(config)
    preset = first.save_preset(demo_preset(config, memory=memory))
    try:
        assert first.list_presets() == [preset]
        result = first.collect(preset.id)
        assert result.inserted_rows == result.total_rows == 5
        rows = first.query(preset.id, "SELECT hostname FROM source.host_inventory WHERE status='warning'")
        assert rows.rows == (("rhel-db-01",),)
        assert first.list_tables(preset.id)[0].name == "source.host_inventory"
        assert first.list_runs()[0].state is RunState.SUCCEEDED
        assert first.last_success(preset.id).id == result.run_id
    finally:
        first.close()
    second = ApplicationService(config)
    try:
        assert second.get_preset(preset.id) == preset
        if memory:
            with pytest.raises(CdwError) as error:
                second.query(preset.id, "SELECT * FROM source.host_inventory")
            assert error.value.code is ErrorCode.QUERY_FAILED
            assert not config.default_database.exists()
        else:
            assert second.query(preset.id, "SELECT count(*) FROM source.host_inventory").rows == ((5,),)
            assert config.default_database.exists()
    finally:
        second.close()


@pytest.mark.parametrize(("format", "raw", "expected"), [
    (OutputFormat.JSON, '[{"name":"서울","count":2},{"name":"부산","count":3}]', (("서울", 2), ("부산", 3))),
    (OutputFormat.JSON, '{"name":"서울","count":2}', (("서울", 2),)),
    (OutputFormat.NDJSON, '{"name":"서울","count":2}\n{"name":"부산","count":3}\n', (("서울", 2), ("부산", 3))),
    (OutputFormat.CSV, 'name,count\n서울,2\n부산,3\n', (("서울", 2), ("부산", 3))),
    (OutputFormat.TEXT, '서울\n부산\n', ((1, "서울"), (2, "부산"))),
])
def test_supported_output_formats(service, format, raw, expected):
    preset = output(service, raw, output_format=format)
    service.collect(preset.id)
    assert service.query(preset.id, f"SELECT * FROM {preset.qualified_table}").rows == expected


def test_replace_append_and_schema_mismatch_preserve_data(service):
    preset = output(service, '[{"id":1}]')
    service.collect(preset.id)
    service.collect(preset.id)
    assert service.query(preset.id, "SELECT count(*) FROM source.host_inventory").rows == ((1,),)
    service.save_preset(replace(preset, write_mode=WriteMode.APPEND))
    assert service.collect(preset.id).total_rows == 2
    service.save_preset(replace(preset, write_mode=WriteMode.APPEND, arguments=("-c", "print('{\"other\": 1}')")))
    with pytest.raises(CdwError) as error:
        service.collect(preset.id)
    assert error.value.code is ErrorCode.SCHEMA_MISMATCH
    assert service.query(preset.id, "SELECT * FROM source.host_inventory").rows == ((1,), (1,))
    assert service.list_runs()[0].state is RunState.FAILED


@pytest.mark.parametrize(("program", "code"), [
    ("import sys; print('[{\"id\":9}]'); sys.stderr.write('diagnostic'); sys.exit(7)", ErrorCode.COMMAND_FAILED),
    ("print('not json')", ErrorCode.PARSE_FAILED),
    ("print('[]')", ErrorCode.EMPTY_OUTPUT),
    ("pass", ErrorCode.EMPTY_OUTPUT),
    ("print('[1, 2]')", ErrorCode.PARSE_FAILED),
])
def test_failed_collection_never_replaces_old_data(service, program, code):
    preset = output(service, '[{"id":1}]')
    original = service.collect(preset.id)
    service.save_preset(replace(preset, arguments=("-c", program)))
    with pytest.raises(CdwError) as error:
        service.collect(preset.id)
    assert error.value.code is code
    assert service.query(preset.id, "SELECT * FROM source.host_inventory").rows == ((1,),)
    assert service.list_runs()[0].error_code == code.value
    assert service.last_success(preset.id).id == original.run_id


def test_environment_working_directory_and_stderr(service, tmp_path):
    program = "import os,json,sys; print(json.dumps({'directory':os.getcwd(),'value':os.environ['CDW_TEST_VALUE']})); sys.stderr.write('warning only')"
    preset = command(service, program, working_directory=str(tmp_path), environment={"CDW_TEST_VALUE": "공백 값"})
    result = service.collect(preset.id)
    assert result.stderr == "warning only"
    assert service.list_runs()[0].stderr == "warning only"
    assert service.query(preset.id, "SELECT * FROM source.host_inventory").rows == ((str(tmp_path), "공백 값"),)


def test_arguments_are_not_interpreted_by_shell(service):
    program = "import sys,json; print(json.dumps({'value':sys.argv[1]}))"
    literal = "space ; $(not_a_command) & literal"
    preset = command(service, program)
    service.save_preset(replace(preset, arguments=("-c", program, literal)))
    service.collect(preset.id)
    assert service.query(preset.id, "SELECT * FROM source.host_inventory").rows == ((literal,),)


@pytest.mark.parametrize(("program", "policy", "code"), [
    ("import time; time.sleep(10)", ExecutionPolicy(0.2, 1024), ErrorCode.TIMEOUT),
    ("print('x' * 100000)", ExecutionPolicy(5, 256), ErrorCode.OUTPUT_LIMIT),
    ("import sys; sys.stderr.write('x' * 100000)", ExecutionPolicy(5, 256), ErrorCode.OUTPUT_LIMIT),
])
def test_execution_limits(service, program, policy, code):
    preset = command(service, program, execution=policy)
    with pytest.raises(CdwError) as error:
        service.collect(preset.id)
    assert error.value.code is code
    assert service.list_runs()[0].state is RunState.FAILED


def test_cancel_running_command_records_cancellation(service):
    preset = command(service, "import time; time.sleep(10)")
    cancel = threading.Event()
    timer = threading.Timer(0.2, cancel.set)
    timer.start()
    try:
        with pytest.raises(CdwError) as error:
            service.collect(preset.id, cancel)
        assert error.value.code is ErrorCode.CANCELLED
        assert service.list_runs()[0].state is RunState.CANCELLED
    finally:
        timer.join()


def test_schema_types_and_preview_limit(tmp_path):
    service = ApplicationService(replace(build_config(tmp_path), query_policy=QueryPolicy(2)))
    try:
        preset = output(service, '[{"id":1,"ok":true},{"id":2,"ok":false},{"id":3,"ok":null}]')
        service.collect(preset.id)
        result = service.query(preset.id, "SELECT * FROM source.host_inventory ORDER BY id")
        assert result.columns == ("id", "ok")
        assert result.types == ("BIGINT", "BOOLEAN")
        assert result.rows == ((1, True), (2, False))
        assert result.truncated is True
        assert service.query(preset.id, "SELECT * FROM source.host_inventory WHERE id=3").rows == ((3, None),)
        assert service.query(preset.id, "SELECT count(*) FROM source.host_inventory").truncated is False
    finally:
        service.close()


@pytest.mark.parametrize("sql", [
    "DROP TABLE source.host_inventory", "DELETE FROM source.host_inventory",
    "CREATE TABLE unwanted (id INTEGER)", "SELECT 1; SELECT 2", "",
    "EXPLAIN ANALYZE DELETE FROM source.host_inventory",
])
def test_sql_is_a_single_select(service, sql):
    preset = output(service, '{"id":1}')
    service.collect(preset.id)
    with pytest.raises(CdwError) as error:
        service.query(preset.id, sql)
    assert error.value.code is ErrorCode.INVALID_QUERY
    assert service.query(preset.id, "WITH result AS (SELECT * FROM source.host_inventory) SELECT * FROM result").rows == ((1,),)


def test_unique_preset_name_and_storage_target(service):
    preset = service.save_preset(demo_preset(service.config))
    for duplicate in (replace(preset, id=str(uuid4()), table_name="another"),
                      replace(preset, id=str(uuid4()), name="another")):
        with pytest.raises(CdwError) as error:
            service.save_preset(duplicate)
        assert error.value.code is ErrorCode.PRESET_CONFLICT
    edited = replace(preset, name="edited")
    service.save_preset(edited)
    assert service.list_presets() == [edited]


def test_preset_serialization_round_trip_and_version(service):
    preset = demo_preset(service.config)
    encoded = json.loads(json.dumps(preset_to_dict(preset)))
    assert preset_from_dict(encoded) == preset
    encoded["version"] = 999
    with pytest.raises(CdwError) as error:
        preset_from_dict(encoded)
    assert error.value.code is ErrorCode.INVALID_PRESET


def test_profile_lock_releases_without_deleting_file(tmp_path):
    first = ProfileLock(tmp_path / "session.lock")
    second = ProfileLock(tmp_path / "session.lock")
    first.acquire()
    try:
        with pytest.raises(CdwError) as error:
            second.acquire()
        assert error.value.code is ErrorCode.PROFILE_BUSY
    finally:
        first.close()
    second.acquire()
    second.close()


@pytest.mark.parametrize("changes", [
    {"arguments": "-c print(1)"}, {"environment": {"VALUE": 123}},
    {"execution": {"timeout_seconds": 1, "max_output_bytes": 1.5}},
    {"execution": {"timeout_seconds": "1", "max_output_bytes": 1024}},
    {"table_name": "bad; DROP TABLE x"}, {"encoding": "not-an-encoding"},
])
def test_invalid_preset_json_has_stable_validation_error(service, changes):
    document = preset_to_dict(demo_preset(service.config))
    document.update(changes)
    with pytest.raises(CdwError) as error:
        preset_from_dict(document)
    assert error.value.code is ErrorCode.INVALID_PRESET


def test_sql_can_be_cancelled_and_connection_reused(service):
    preset = output(service, '{"id":1}')
    service.collect(preset.id)
    cancel = threading.Event()

    def interrupt():
        cancel.set()
        service.interrupt_query()

    timer = threading.Timer(0.2, interrupt)
    timer.start()
    try:
        with pytest.raises(CdwError) as error:
            service.query(preset.id, "SELECT sum(a.i * b.i) FROM range(1000000) a(i), range(1000000) b(i)", cancel)
        assert error.value.code is ErrorCode.CANCELLED
    finally:
        timer.join()
    assert service.query(preset.id, "SELECT * FROM source.host_inventory").rows == ((1,),)


def test_interrupted_process_is_recovered_without_replacing_data(tmp_path):
    config = build_config(tmp_path)
    service = ApplicationService(config)
    preset = output(service, '{"id":1}')
    service.collect(preset.id)
    service.close()
    program = (
        "import os,sys; from pathlib import Path; "
        "from cdw.application.service import ApplicationService; from cdw.config import build_config; "
        "from cdw.domain.contracts import RunState; "
        "service=ApplicationService(build_config(Path(sys.argv[1]))); "
        "service.collect(sys.argv[2], on_progress=lambda p: os._exit(23) if p.state is RunState.PARSING else None)"
    )
    crashed = subprocess.run([sys.executable, "-c", program, str(tmp_path), preset.id], timeout=15)
    assert crashed.returncode == 23
    recovered = ApplicationService(config)
    try:
        recovered.recover()
        assert recovered.list_runs()[0].state is RunState.INTERRUPTED
        assert recovered.query(preset.id, "SELECT * FROM source.host_inventory").rows == ((1,),)
    finally:
        recovered.close()
