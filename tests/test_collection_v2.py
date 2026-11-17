"""Collection contracts exercised with real output, processes and persisted tables."""

import json
import os
import shutil
import sys
import threading
from dataclasses import replace
from uuid import uuid4

import polars as pl
import pytest

from cdw.application.service import ApplicationService
from cdw.config import build_config
from cdw.domain.contracts import (
    CdwError, CommandMode, CommandSpec, ErrorCode, ExecutionPolicy, OutputFormat,
    Preset, QueryPolicy, StorageMode, StorageTarget, WriteMode,
)
from cdw.domain.parsing import ColumnCountMode, DelimiterMode, ParseSpec
from cdw.domain.serialization import migrate_preset_document_v1, preset_from_dict, preset_to_dict
from cdw.infrastructure.parsers import OutputParser
from cdw.infrastructure.runner import SubprocessRunner


def collection_preset(raw="name,count\nalpha,2\n", **changes):
    values = dict(
        id=str(uuid4()), name="Command collection", executable=sys.executable,
        arguments=("-c", f"import sys; sys.stdout.buffer.write({raw.encode('utf-8')!r})"),
        working_directory=None, environment=dict(os.environ), output_format=OutputFormat.CSV,
        encoding="utf-8", csv_delimiter=",", storage=StorageTarget(StorageMode.MEMORY, None),
        table_name="collected_data", write_mode=WriteMode.REPLACE,
        execution=ExecutionPolicy(5, 1024 * 1024),
    )
    values.update(changes)
    return Preset(**values)


@pytest.mark.parametrize(("raw", "parsing", "delimiter", "columns", "rows"), [
    ('name,count\n"서울, 본사",2\n부산,3\n', ParseSpec(), ",", ("name", "count"), (("서울, 본사", 2), ("부산", 3))),
    ("alpha\t2\nbeta\t3\n", ParseSpec(has_header=False), "\t", ("column_1", "column_2"), (("alpha", 2), ("beta", 3))),
    ("host   cpu\n alpha\t  2\n beta 3\n", ParseSpec(delimiter_mode=DelimiterMode.WHITESPACE), ",", ("host", "cpu"), (("alpha", 2), ("beta", 3))),
    ("old,head\nalpha,2\n", ParseSpec(column_mode=ColumnCountMode.FIXED, column_count=2, column_names=("host", "count")), ",", ("host", "count"), (("alpha", 2),)),
    ("alpha,2\n", ParseSpec(has_header=False, column_mode=ColumnCountMode.FIXED, column_count=2), ",", ("column_1", "column_2"), (("alpha", 2),)),
    ('name,value\n"line\nbreak",2\n', ParseSpec(), ",", ("name", "value"), (("line\nbreak", 2),)),
])
def test_delimited_output_has_the_requested_shape(raw, parsing, delimiter, columns, rows):
    preset = collection_preset(raw, parsing=parsing, csv_delimiter=delimiter)
    frame = OutputParser().parse(raw.encode("utf-8"), preset)
    assert tuple(frame.columns) == columns
    assert tuple(frame.iter_rows()) == rows


def test_string_policy_preserves_identifiers_and_date_text():
    raw = "id,amount,date\n0012,1.50,2026-11-17\n"
    preset = collection_preset(raw, parsing=ParseSpec(infer_types=False))
    frame = OutputParser().parse(raw.encode(), preset)
    assert frame.dtypes == [pl.String, pl.String, pl.String]
    assert tuple(frame.iter_rows()) == (("0012", "1.50", "2026-11-17"),)


@pytest.mark.parametrize("raw", ["name,count\nalpha\n", "name,count\nalpha,2,extra\n", "name,count\nalpha,2\nbeta\n"])
def test_ragged_rows_are_errors_instead_of_silent_truncation(raw):
    with pytest.raises(CdwError) as failure:
        OutputParser().parse(raw.encode(), collection_preset(raw))
    assert failure.value.code is ErrorCode.PARSE_FAILED
    assert failure.value.details


def test_fixed_count_validates_header_even_when_custom_names_replace_it():
    raw = "old,head,extra\nalpha,2\n"
    preset = collection_preset(raw, parsing=ParseSpec(column_mode=ColumnCountMode.FIXED,
                                                     column_count=2, column_names=("host", "count")))
    with pytest.raises(CdwError) as failure:
        OutputParser().parse(raw.encode(), preset)
    assert failure.value.code is ErrorCode.PARSE_FAILED


@pytest.mark.parametrize("raw", ["name,Name\na,b\n", "name,\na,b\n", 'name,value\n"unclosed,2\n'])
def test_ambiguous_headers_and_invalid_csv_are_rejected(raw):
    with pytest.raises(CdwError) as failure:
        OutputParser().parse(raw.encode(), collection_preset(raw))
    assert failure.value.code is ErrorCode.PARSE_FAILED


@pytest.mark.parametrize("raw", ["", " \n", "name,count\n"])
def test_empty_collection_is_not_a_destructive_empty_table(raw):
    with pytest.raises(CdwError) as failure:
        OutputParser().parse(raw.encode(), collection_preset(raw))
    assert failure.value.code is ErrorCode.EMPTY_OUTPUT


@pytest.mark.parametrize("values", [
    {"has_header": "yes"}, {"column_mode": "fixed"},
    {"column_mode": ColumnCountMode.FIXED, "column_count": 0},
    {"column_mode": ColumnCountMode.FIXED, "column_count": True},
    {"column_mode": ColumnCountMode.FIXED, "column_count": 2, "column_names": ("one",)},
    {"column_mode": ColumnCountMode.FIXED, "column_count": 2, "column_names": ("same", "SAME")},
    {"column_names": ("fixed-name-in-dynamic-mode",)},
])
def test_parsing_policy_rejects_inconsistent_public_inputs(values):
    with pytest.raises(ValueError):
        ParseSpec(**values)


def test_command_uses_exact_saved_environment_and_does_not_reintroduce_removed_keys(monkeypatch):
    monkeypatch.setenv("CDW_REMOVED_VARIABLE", "must not leak")
    program = "import json,os; print(json.dumps([os.environ.get('CDW_REMOVED_VARIABLE'), os.environ.get('CDW_SAVED_VARIABLE')]))"
    environment = {key: value for key, value in os.environ.items() if key != "CDW_REMOVED_VARIABLE"}
    environment["CDW_SAVED_VARIABLE"] = "saved value"
    command = CommandSpec(sys.executable, ("-c", program), None, environment, ExecutionPolicy(5, 4096), "utf-8")
    environment["CDW_SAVED_VARIABLE"] = "edited after registration"
    output = SubprocessRunner().run(command, threading.Event())
    assert json.loads(output.stdout) == [None, "saved value"]


def test_environment_snapshot_accepts_process_names_that_are_not_shell_identifiers():
    command = CommandSpec(sys.executable, (), None, {"BASH_FUNC_mytool%%": "() { echo hi; }"},
                          ExecutionPolicy(5, 4096), "utf-8")
    assert command.environment["BASH_FUNC_mytool%%"] == "() { echo hi; }"


@pytest.mark.parametrize("environment", [{"": "value"}, {"A=B": "value"}, {"NAME": "a\0b"}, {"A\0B": "value"}])
def test_process_environment_rejects_values_the_os_cannot_execute(environment):
    with pytest.raises(CdwError) as failure:
        CommandSpec(sys.executable, (), None, environment, ExecutionPolicy(5, 4096), "utf-8")
    assert failure.value.code is ErrorCode.INVALID_PRESET


def test_preview_can_return_nonzero_stdout_and_stderr_without_changing_collection_failure_semantics():
    command = collection_preset(arguments=("-c", "import sys; print('name,count'); sys.stderr.write('diagnostic'); sys.exit(9)")).command
    output = SubprocessRunner().run(command, threading.Event(), allow_nonzero=True)
    assert output.return_code == 9
    assert output.stdout.strip() == b"name,count"
    assert output.stderr == b"diagnostic"
    with pytest.raises(CdwError) as failure:
        SubprocessRunner().run(command, threading.Event())
    assert failure.value.code is ErrorCode.COMMAND_FAILED


@pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="The explicit production shell is Linux Bash")
def test_explicit_shell_pipeline_uses_pipefail():
    command = CommandSpec(shutil.which("bash"), (), None, dict(os.environ), ExecutionPolicy(5, 4096), "utf-8",
                          mode=CommandMode.SHELL, script="false | cat")
    with pytest.raises(CdwError) as failure:
        SubprocessRunner().run(command, threading.Event())
    assert failure.value.code is ErrorCode.COMMAND_FAILED
    success = replace(command, script="printf 'host count\\nalpha 2\\n' | cat")
    assert SubprocessRunner().run(success, threading.Event()).stdout == b"host count\nalpha 2\n"


def test_version_two_document_round_trip_preserves_explicit_settings():
    preset = collection_preset(executable="/bin/bash", arguments=(), command_mode=CommandMode.SHELL,
                               shell_script="printf 'alpha 2\\n'", environment={"LANG": "C.UTF-8"},
                               parsing=ParseSpec(has_header=False, column_mode=ColumnCountMode.FIXED,
                                                 column_count=2, column_names=("host", "count"),
                                                 delimiter_mode=DelimiterMode.WHITESPACE))
    document = json.loads(json.dumps(preset_to_dict(preset)))
    assert document["version"] == 2
    assert preset_from_dict(document) == preset


def test_legacy_migration_freezes_environment_once_without_a_permanent_reader_fallback():
    legacy = preset_to_dict(collection_preset(environment={"OVERRIDE": "original"}))
    legacy["version"] = 1
    for key in ("command_mode", "shell_script", "parsing"):
        legacy.pop(key)
    original = json.loads(json.dumps(legacy))
    migrated = migrate_preset_document_v1(legacy, {"BASE": "captured", "OVERRIDE": "host"})
    assert legacy == original
    assert migrated["environment"] == {"BASE": "captured", "OVERRIDE": "original"}
    assert preset_from_dict(migrated).command_mode is CommandMode.DIRECT
    with pytest.raises(CdwError) as failure:
        preset_from_dict(legacy)
    assert failure.value.code is ErrorCode.INVALID_PRESET


def test_preview_and_collection_share_shape_without_preview_writes(tmp_path):
    config = replace(build_config(tmp_path), query_policy=QueryPolicy(1))
    service = ApplicationService(config)
    preset = collection_preset("host   count\nalpha 2\nbeta 3\n", parsing=ParseSpec(delimiter_mode=DelimiterMode.WHITESPACE))
    try:
        preview = service.preview(preset)
        assert preview.parse_error == ""
        assert preview.columns == ("host", "count")
        assert preview.rows == (("alpha", 2),)
        assert preview.total_rows == 2
        assert service.list_presets() == []
        assert service.list_runs() == []
        assert service.workspace_tables() == []
        assert not config.default_database.exists()
        service.save_preset(preset)
        service.collect(preset.id)
        query = service.query(preset.id, f"SELECT * FROM {preset.qualified_table}")
        assert query.columns == preview.columns
        assert query.rows == preview.rows
        assert service.query(preset.id, f"SELECT COUNT(*) FROM {preset.qualified_table}").rows == ((2,),)
    finally:
        service.close()


def test_preview_returns_original_output_for_parse_failure_and_never_mutates_previous_table(tmp_path):
    service = ApplicationService(build_config(tmp_path))
    preset = collection_preset()
    try:
        service.save_preset(preset)
        service.collect(preset.id)
        before_runs = service.list_runs()
        invalid = replace(preset, arguments=collection_preset("name,count\nalpha,2,extra\n").arguments)
        preview = service.preview(invalid)
        assert preview.parse_error
        assert preview.stdout == "name,count\nalpha,2,extra\n"
        assert preview.rows == ()
        assert service.get_preset(preset.id) == preset
        assert service.list_runs() == before_runs
        assert service.query(preset.id, f"SELECT * FROM {preset.qualified_table}").rows == (("alpha", 2),)
    finally:
        service.close()
