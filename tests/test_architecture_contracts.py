"""Public contracts for language packs, pages, execution and adapter boundaries."""

import ast
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from qtpy.QtWidgets import QApplication

from cdw.application.service import ApplicationService
from cdw.application.tasks import TaskService
from cdw.config import build_config
from cdw.domain.contracts import CdwError, CommandSpec, ErrorCode, ExecutionPolicy, Progress, RunState
from cdw.domain.filtering import ColumnKind
from cdw.domain.tasks import TaskDefinition, TaskInputFormat
from cdw.i18n import Catalog
from cdw.infrastructure.runner import SubprocessRunner
from cdw.server.client import RemoteApplicationService
from cdw.ui.controller import Controller, Operation
from cdw.ui.main_window import MainWindow
from cdw.ui.pages.base import BasePage, PageId
from helpers import stop_server


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def wait_for(application, predicate):
    deadline = time.monotonic() + 10
    while not predicate() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    assert predicate()


def test_partial_language_pack_falls_back_and_preserves_named_values(tmp_path):
    pack = tmp_path / "en.json"
    pack.write_text(json.dumps({"common.cancel": "Cancel", "app.footer": "v{version} · OFFLINE"}), encoding="utf-8")
    catalog = Catalog.load(pack)
    assert catalog.text("common.cancel") == "Cancel"
    assert catalog.text("common.edit") == "편집"
    assert catalog.text("app.footer").format(version="1.2") == "v1.2 · OFFLINE"


@pytest.mark.parametrize("overrides", [
    {"unknown.key": "text"}, {"app.footer": "no placeholder"},
    {"app.footer": "{wrong}"}, {"app.footer": "{version"},
    {"common.cancel": 42}, ["not a mapping"],
])
def test_invalid_language_packs_are_rejected(tmp_path, overrides):
    pack = tmp_path / "invalid.json"
    pack.write_text(json.dumps(overrides), encoding="utf-8")
    with pytest.raises(ValueError):
        Catalog.load(pack)


def test_selected_pack_changes_actual_widgets_on_startup(tmp_path):
    pack = tmp_path / "en.json"
    pack.write_text(json.dumps({"pages.sql.title": "SQL Workspace", "sidebar.expand": "Expand sidebar"}), encoding="utf-8")
    program = """
import json
from qtpy.QtWidgets import QApplication
from cdw.ui.pages.sql import SqlPage
from cdw.ui.widgets import CdwSidebar
app = QApplication([])
sidebar = CdwSidebar()
sidebar.set_collapsed(True)
print(json.dumps([SqlPage.SPEC.navigation, sidebar.toggle_button.toolTip()]))
"""
    environment = dict(os.environ, CDW_LANGUAGE_PACK=str(pack), QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([sys.executable, "-c", program], env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["SQL Workspace", "Expand sidebar"]


def test_application_messages_are_catalog_keys():
    root = Path(__file__).parents[1] / "src/cdw"
    catalog = Catalog.load()
    for path in root.rglob("*.py"):
        if "i18n" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not any("가" <= char <= "힣" for char in node.value), (path, node.lineno)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr":
                assert isinstance(node.args[0], ast.Constant)
                assert isinstance(catalog.text(node.args[0].value), str)


def test_runner_accepts_a_command_without_a_preset(tmp_path):
    environment = {"CDW_TEST_VALUE": "original"}
    command = CommandSpec(sys.executable, ("-c", "import os; print(os.environ['CDW_TEST_VALUE'])"),
                          str(tmp_path), environment, ExecutionPolicy(5, 4096), "utf-8")
    environment["CDW_TEST_VALUE"] = "changed"
    output = SubprocessRunner().run(command, threading.Event())
    assert output.stdout.strip() == b"original"
    assert output.return_code == 0
    with pytest.raises(TypeError):
        command.environment["CDW_TEST_VALUE"] = "changed"


def test_task_resolves_its_input_using_the_shared_command_contract(tmp_path):
    task = TaskDefinition("task", "Task", sys.executable, ("{input}",), None, {},
                          TaskInputFormat.JSON, ",", ExecutionPolicy(5, 4096))
    command = task.command_for_input(str(tmp_path / "input.json"))
    assert isinstance(command, CommandSpec)
    assert command.arguments == (str(tmp_path / "input.json"),)
    assert not hasattr(command, "storage") and not hasattr(command, "table_name")


def test_metadata_initializes_complete_schema_without_repository_order(tmp_path):
    config = build_config(tmp_path)
    tasks = TaskService(config)
    task = TaskDefinition("task", "Task", sys.executable, ("{input}",), None, {},
                          TaskInputFormat.JSON, ",", ExecutionPolicy(5, 4096))
    tasks.save_task(task)
    with sqlite3.connect(config.metadata_database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"presets", "runs", "app_settings", "workspace_tasks", "workflows", "schedules", "workflow_runs"} <= tables
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
    service = ApplicationService(config)
    try:
        assert TaskService(config, database=service.database).get_task("task") == task
        assert service.list_presets() == []
    finally:
        service.close()


def test_database_returns_semantic_kinds_for_real_query_results(tmp_path):
    service = ApplicationService(build_config(tmp_path))
    try:
        result = service.query_workspace(None, "SELECT 2::DECIMAL(10,2) AS n, DATE '2026-01-01' AS d, TIMESTAMP_NS '2026-01-01' AS t, 'hi' AS s, true AS b, [1,2] AS a").result
        assert result.kinds == (ColumnKind.NUMBER, ColumnKind.DATE, ColumnKind.DATETIME,
                                ColumnKind.TEXT, ColumnKind.BOOLEAN, ColumnKind.OTHER)
        assert result.types[0] == "DECIMAL(10,2)"
    finally:
        service.close()


def test_controller_finishes_and_recovers_without_a_main_window(application):
    owner_thread = threading.get_ident()
    closed_on = []
    service = SimpleNamespace(close=lambda: closed_on.append(threading.get_ident()))
    controller = Controller(service, None)
    events = []
    controller.completed.connect(lambda operation, value: events.append((operation, value, controller.busy, threading.get_ident())))
    controller.failed.connect(lambda operation, error: events.append((operation, error.code, controller.busy, threading.get_ident())))
    gate = threading.Event()
    try:
        assert controller.submit(Operation.QUERY, lambda: (gate.wait(5), 7)[1])
        assert controller.busy
        assert not controller.submit(Operation.QUERY, lambda: 8)
        gate.set()
        wait_for(application, lambda: len(events) == 1)
        assert events[0] == (Operation.QUERY, 7, False, owner_thread)
        def fail():
            raise CdwError(ErrorCode.INVALID_QUERY)
        assert controller.submit(Operation.QUERY, fail)
        wait_for(application, lambda: len(events) == 2)
        assert events[1] == (Operation.QUERY, ErrorCode.INVALID_QUERY, False, owner_thread)
        assert controller.submit(Operation.CONTEXT, lambda: [])
        wait_for(application, lambda: len(events) == 3)
    finally:
        gate.set()
        wait_for(application, lambda: not controller.busy)
        controller.shutdown()
    assert len(closed_on) == 1 and closed_on[0] != owner_thread


def test_saving_cannot_be_cancelled_and_shutdown_blocks_new_work(application):
    release = threading.Event()
    cancellation = []
    def collect(preset_id, cancel, report):
        report(Progress("run", RunState.SAVING, "saving"))
        release.wait(5)
        cancellation.append(cancel.is_set())
        return "saved"
    service = SimpleNamespace(collect=collect, workspace_tables=lambda: [], close=lambda: None)
    controller = Controller(service, None)
    try:
        assert controller.collect("preset")
        wait_for(application, lambda: controller.state.stage is RunState.SAVING)
        assert not controller.state.can_cancel and not controller.cancel()
        controller.stop_accepting()
        assert not controller.submit(Operation.QUERY, lambda: None)
        release.set()
        wait_for(application, lambda: not controller.busy)
        assert cancellation == [False]
    finally:
        release.set()
        wait_for(application, lambda: not controller.busy)
        controller.shutdown()


def test_controller_cancels_running_work_and_rejects_foreign_thread_calls(application):
    observed = []
    def collect(preset_id, cancel, report):
        if cancel.wait(5):
            raise CdwError(ErrorCode.CANCELLED)
        raise AssertionError("Cancellation was not delivered")
    service = SimpleNamespace(collect=collect, workspace_tables=lambda: [], close=lambda: None)
    controller = Controller(service, None)
    controller.failed.connect(lambda operation, error: observed.append(error.code))
    failures = []
    def foreign_thread():
        try:
            controller.submit(Operation.QUERY, lambda: None)
        except RuntimeError as error:
            failures.append(str(error))
    worker = threading.Thread(target=foreign_thread)
    worker.start()
    worker.join(2)
    assert failures and not controller.busy
    try:
        assert controller.collect("preset")
        assert controller.cancel()
        assert not controller.cancel()
        wait_for(application, lambda: not controller.busy)
        assert observed == [ErrorCode.CANCELLED]
    finally:
        controller.cancel()
        wait_for(application, lambda: not controller.busy)
        controller.shutdown()


def test_pages_preserve_editor_tabs_and_memory_session_across_navigation(application, tmp_path):
    with pytest.raises(TypeError):
        BasePage()
    service = RemoteApplicationService(build_config(tmp_path))
    window = None
    try:
        window = MainWindow(service)
        wait_for(application, lambda: not window.controller.busy)
        assert all(isinstance(window.page(page_id), BasePage) for page_id in PageId)
        page = window.page(PageId.SQL)
        window.show_page(PageId.SQL)
        page.sql_editor.setPlainText("SELECT 42 AS value")
        page.run_query()
        wait_for(application, lambda: not window.controller.busy)
        pane = page.result_tabs.current_pane()
        assert window.controller.save_result_table(pane.snapshot.id, "saved_value", pane.row_indices())
        wait_for(application, lambda: not window.controller.busy)
        for page_id in (PageId.SETTINGS, PageId.HOME, PageId.TASKS, PageId.SQL):
            window.show_page(page_id)
        assert window.page(PageId.SQL) is page
        assert page.sql_editor.toPlainText() == "SELECT 42 AS value"
        assert page.result_tabs.current_pane() is pane
        page.sql_editor.setPlainText("SELECT * FROM memory.saved_value")
        page.run_query()
        wait_for(application, lambda: not window.controller.busy)
        assert page.result_tabs.current_pane().current_result().rows == ((42,),)
    finally:
        try:
            if window is not None:
                wait_for(application, lambda: not window.controller.busy)
                window.close()
                application.processEvents()
        finally:
            service.close()
            stop_server(service.config)
