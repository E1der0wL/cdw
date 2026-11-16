"""Task contracts through saved definitions, real subprocesses and the editor."""

import json
import os
import sys
import threading
import time
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from qtpy.QtCore import Qt, QTimer
from qtpy.QtGui import QFontDatabase
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication

from cdw.application.tasks import TaskService
from cdw.config import build_config
from cdw.domain.contracts import CdwError, ErrorCode, ExecutionPolicy, QueryResult
from cdw.domain.tasks import TaskDefinition, TaskInputFormat
from cdw.ui.task_dialog import TaskDialog
from cdw.ui.tasks_page import TasksPage


def definition(program="import sys; print(sys.argv[1])", **changes):
    task = TaskDefinition(
        id="sample-task", name="샘플 작업", executable=sys.executable, arguments=("-c", program, "{input}"),
        working_directory=None, environment={}, input_format=TaskInputFormat.JSON, csv_delimiter=",",
        execution=ExecutionPolicy(5, 1024 * 1024),
    )
    return replace(task, **changes)


def table(rows=(("서울", 2), ("부산", None))):
    return QueryResult(("name", "count"), ("VARCHAR", "BIGINT"), rows, False, 0)


def test_task_persistence_update_delete_and_duplicate_name(tmp_path):
    config = build_config(tmp_path)
    service = TaskService(config)
    original = service.save_task(definition(environment={"TASK_MODE": "original"}))
    reopened = TaskService(config)
    assert reopened.list_tasks() == [original]
    updated = reopened.save_task(replace(original, name="renamed", environment={"TASK_MODE": "updated"}))
    assert service.get_task(original.id) == updated
    with pytest.raises(CdwError):
        service.save_task(replace(original, id="other", name="RENAMED"))
    assert service.list_tasks() == [updated]
    service.delete_task(original.id)
    assert reopened.list_tasks() == []
    with pytest.raises(CdwError) as error:
        service.get_task(original.id)
    assert error.value.code is ErrorCode.NOT_FOUND


def test_json_task_receives_typed_full_table_and_input_is_removed(tmp_path):
    service = TaskService(build_config(tmp_path))
    program = "import json,sys,os; p=sys.argv[1]; data=json.load(open(p,encoding='utf-8')); print(json.dumps({'path':p,'input':data,'env':os.environ['TASK_MODE']}))"
    task = service.save_task(definition(program, environment={"TASK_MODE": "received"}))
    result = QueryResult(("id", "amount", "day", "blob"), ("INTEGER", "DECIMAL(38,4)", "DATE", "BLOB"),
                         tuple((i, Decimal("1000000000000000.0001"), date(2026, 11, 14), b"\x00\xff") for i in range(650)), False, 0)
    outcome = service.run_task(task.id, result)
    received = json.loads(outcome.stdout)
    assert received["env"] == "received"
    assert received["input"]["columns"] == list(result.columns)
    assert received["input"]["types"] == list(result.types)
    assert len(received["input"]["rows"]) == 650
    assert received["input"]["rows"][0] == [0, "1000000000000000.0001", "2026-11-14", {"$binary": "AP8="}]
    assert not Path(received["path"]).exists()
    assert outcome.return_code == 0 and outcome.elapsed_seconds >= 0
    assert outcome.task_id == task.id and outcome.task_name == task.name


def test_csv_delimiter_unicode_quotes_newlines_and_empty_result(tmp_path):
    service = TaskService(build_config(tmp_path))
    program = "import csv,json,sys; p=sys.argv[1]; print(json.dumps({'path':p,'rows':list(csv.reader(open(p,encoding='utf-8',newline=''),delimiter=';'))}))"
    task = service.save_task(definition(program, input_format=TaskInputFormat.CSV, csv_delimiter=";"))
    outcome = service.run_task(task.id, table((("a;\"b\n서울", 2), ("", None))))
    received = json.loads(outcome.stdout)
    assert received["rows"] == [["name", "count"], ['a;"b\n서울', "2"], ["", ""]]
    assert not Path(received["path"]).exists()
    assert json.loads(service.run_task(task.id, table(())).stdout)["rows"] == [["name", "count"]]


def test_task_csv_uses_shared_export_delimiter_contract(tmp_path):
    service = TaskService(build_config(tmp_path))
    program = "import csv,json,sys; print(json.dumps(list(csv.reader(open(sys.argv[1],encoding='utf-8',newline=''),delimiter='│'))))"
    task = service.save_task(definition(program, input_format=TaskInputFormat.CSV, csv_delimiter="│"))
    assert json.loads(service.run_task(task.id, table()).stdout) == [["name", "count"], ["서울", "2"], ["부산", ""]]
    with pytest.raises(ValueError):
        definition(csv_delimiter='"')


@pytest.mark.parametrize("arguments", [("--input",), (), (None, "{input}")])
def test_missing_or_invalid_input_argument_rejected(arguments):
    with pytest.raises((ValueError, CdwError)):
        definition(arguments=arguments)


@pytest.mark.parametrize("input_format", [TaskInputFormat.JSON, TaskInputFormat.CSV])
def test_task_output_failure_and_timeout_remove_temporary_input(tmp_path, input_format):
    service = TaskService(build_config(tmp_path))
    marker = tmp_path / "input_path.txt"
    mark = f"import pathlib,sys; pathlib.Path({str(marker)!r}).write_text(sys.argv[1],encoding='utf-8'); "
    for program, policy, expected in (
        (mark + "sys.stderr.write('task failure'); sys.exit(7)", ExecutionPolicy(5, 1024), ErrorCode.COMMAND_FAILED),
        (mark + "import time; time.sleep(5)", ExecutionPolicy(0.2, 1024), ErrorCode.TIMEOUT),
        (mark + "sys.stdout.write('x'*4096)", ExecutionPolicy(5, 256), ErrorCode.OUTPUT_LIMIT),
    ):
        task = service.save_task(definition(program, execution=policy, input_format=input_format))
        with pytest.raises(CdwError) as error:
            service.run_task(task.id, table())
        assert error.value.code is expected
        assert not Path(marker.read_text(encoding="utf-8")).exists()


@pytest.mark.parametrize("input_format", [TaskInputFormat.JSON, TaskInputFormat.CSV])
def test_running_task_cancellation_cleans_input_and_later_runs_work(tmp_path, input_format):
    service = TaskService(build_config(tmp_path))
    marker = tmp_path / "input_path.txt"
    program = f"import pathlib,sys,time; source=open(sys.argv[1],encoding='utf-8'); source.read(); pathlib.Path({str(marker)!r}).write_text(sys.argv[1],encoding='utf-8'); time.sleep(10)"
    task = service.save_task(definition(program, execution=ExecutionPolicy(20, 1024), input_format=input_format))
    cancel = threading.Event()
    errors = []

    def execute():
        try:
            service.run_task(task.id, table(), cancel)
        except CdwError as exc:
            errors.append(exc)

    worker = threading.Thread(target=execute)
    worker.start()
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    cancel.set()
    worker.join(10)
    assert not worker.is_alive()
    assert marker.exists()
    assert errors and errors[0].code is ErrorCode.CANCELLED
    assert not Path(marker.read_text(encoding="utf-8")).exists()
    service.save_task(definition())
    assert service.run_task(task.id, table()).return_code == 0


def test_truncated_input_is_not_silently_processed(tmp_path):
    service = TaskService(build_config(tmp_path))
    task = service.save_task(definition())
    with pytest.raises(CdwError) as error:
        service.run_task(task.id, replace(table(), truncated=True))
    assert error.value.code is ErrorCode.INVALID_QUERY


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    if os.name == "nt" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        for name in ("malgun.ttf", "malgunbd.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / name))
    return app


def test_tasks_page_registers_and_edits_without_executing(app, tmp_path):
    service = TaskService(build_config(tmp_path))
    page = TasksPage(service)
    page.show()
    app.processEvents()
    failures = []

    def register():
        dialog = app.activeModalWidget()
        try:
            assert isinstance(dialog, TaskDialog)
            dialog.name_edit.setText("입력 확인")
            dialog.executable_edit.setText(sys.executable)
            dialog.arguments_edit.setPlainText("-c\nimport sys; print(sys.argv[1])\n{input}")
            QTest.mouseClick(dialog.save_button, Qt.LeftButton)
        except Exception as exc:
            failures.append(exc)
            if dialog is not None:
                dialog.reject()

    try:
        QTimer.singleShot(50, register)
        QTest.mouseClick(page.new_button, Qt.LeftButton)
        assert not failures, failures
        assert page.table.rowCount() == 1
        assert service.list_tasks()[0].name == "입력 확인"
        page.table.selectRow(0)

        def edit():
            dialog = app.activeModalWidget()
            assert isinstance(dialog, TaskDialog)
            dialog.name_edit.setText("변경된 작업")
            QTest.mouseClick(dialog.save_button, Qt.LeftButton)

        QTimer.singleShot(50, edit)
        QTest.mouseClick(page.edit_button, Qt.LeftButton)
        assert service.list_tasks()[0].name == "변경된 작업"
        assert page.table.item(0, 0).text() == "변경된 작업"
        page.set_busy(True)
        assert not page.new_button.isEnabled() and not page.delete_button.isEnabled()
        page.set_busy(False)
        assert page.new_button.isEnabled()
    finally:
        page.close()
        app.processEvents()


def test_task_editor_csv_input_and_invalid_settings(app, tmp_path):
    dialog = TaskDialog(TaskService(build_config(tmp_path)))
    dialog.show()
    app.processEvents()
    try:
        assert not dialog.delimiter_edit.isEnabled()
        dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(TaskInputFormat.CSV))
        assert dialog.delimiter_edit.isEnabled()
        dialog.name_edit.setText("검증")
        dialog.executable_edit.setText(sys.executable)
        dialog.arguments_edit.setPlainText("-c\nprint('no input')")
        QTest.mouseClick(dialog.save_button, Qt.LeftButton)
        assert dialog.isVisible()
        assert dialog.saved_task is None
        assert "{input}" in dialog.feedback.text()
    finally:
        dialog.close()
        app.processEvents()
