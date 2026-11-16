"""The user's tab → filter → share/save/task workflow through actual Qt controls."""

import csv
import io
import json
import os
import sys
import time
from pathlib import Path

import duckdb
import pytest
from qtpy.QtCore import Qt, QTimer
from qtpy.QtGui import QFontDatabase
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication, QTabBar

from cdb.application.examples import demo_preset
from cdb.application.service import ApplicationService
from cdb.config import build_config
from cdb.domain.contracts import CdbError, ErrorCode, ExecutionPolicy
from cdb.domain.filtering import FilterOperator
from cdb.domain.tasks import TaskDefinition, TaskInputFormat
from cdb.domain.workspace import ExportFormat
from cdb.ui.main_window import MainWindow, PAGE_SQL, PAGE_TASKS
from cdb.ui.result_actions import ChooseTaskDialog, ExportResultDialog, SaveMemoryDialog
from cdb.ui.widgets import CdbMenu


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    if os.name == "nt" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        for filename in ("malgun.ttf", "malgunbd.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
    return app


def idle(app, window):
    deadline = time.monotonic() + 20
    while window.controller.busy and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    app.processEvents()
    assert not window.controller.busy, window.log_view.toPlainText()


@pytest.fixture
def window(application, tmp_path):
    window = MainWindow(ApplicationService(build_config(tmp_path)))
    window.show()
    idle(application, window)
    window.show_page(PAGE_SQL)
    yield window
    idle(application, window)
    window.close()
    application.processEvents()


def query(app, window, sql):
    window.show_page(PAGE_SQL)
    window.sql_editor.setPlainText(sql)
    before = window.result_tabs.count()
    QTest.mouseClick(window.query_button, Qt.LeftButton)
    idle(app, window)
    assert window.result_tabs.count() == before + 1, window.log_view.toPlainText()
    assert window.status_label.text().endswith("SQL 조회 완료")
    return window.result_tabs.current_pane()


def modal(app, action, dialog_type, fill):
    errors = []

    def complete():
        dialog = app.activeModalWidget()
        try:
            assert isinstance(dialog, dialog_type)
            fill(dialog)
            QTest.mouseClick(dialog.submit_button, Qt.LeftButton)
            assert not dialog.isVisible(), dialog.feedback.text()
        except Exception as exc:
            errors.append(exc)
            if dialog:
                dialog.reject()

    QTimer.singleShot(30, complete)
    action()
    assert not errors, errors


def capture(app, widget, name):
    if folder := os.environ.get("CDB_CAPTURE_DIR"):
        app.processEvents()
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        assert widget.grab().save(str(path / f"{name}.png"))


def test_tabs_filter_share_memory_and_task_round_trip(application, window, tmp_path):
    preset = window.service.save_preset(demo_preset(window.service.config))
    window.refresh_presets(preset.id)
    idle(application, window)
    QTest.mouseClick(window.collect_button, Qt.LeftButton)
    idle(application, window)
    sql = "SELECT hostname, cpu_percent, memory_percent, status FROM source.host_inventory"
    first = query(application, window, sql)
    second = query(application, window, sql)
    assert [window.result_tabs.tabText(i) for i in range(2)] == ["host_inventory", "host_inventory (2)"]
    assert first is not second and first.source_model.rowCount() == second.source_model.rowCount() == 5
    assert window.schema_tree.topLevelItem(0).text(0) == "파일"
    assert window.schema_tree.topLevelItem(0).text(2) != "—"

    # Use the column's real button and popup rather than setting a proxy filter.
    QTest.mouseClick(second.header.viewport(), Qt.LeftButton, pos=second.header.button_rect(1).center())
    popup = second.filter_popup
    popup.operator_combo.setCurrentIndex(popup.operator_combo.findData(FilterOperator.GREATER_EQUAL.value))
    popup.lower_edit.setText("30")
    QTest.mouseClick(popup.apply_button, Qt.LeftButton)
    second.open_filter(1)
    QTest.mouseClick(second.filter_popup.descending_button, Qt.LeftButton)
    expected = ("rhel-db-01", "rhel-app-02", "rhel-log-01")
    assert tuple(row[0] for row in second.current_result().rows) == expected
    assert len(first.current_result().rows) == 5
    second.view.setCurrentIndex(second.proxy.index(0, 0))
    # Finish popup teardown and queued focus/layout changes before the next click.
    application.processEvents()

    # Choose the actual share menu action; copying includes the whole filtered table.
    errors = []
    def copy_from_menu():
        menu = application.activePopupWidget()
        try:
            assert isinstance(menu, CdbMenu)
            action = next(action for action in menu.actions() if action.text() == "텍스트로 복사")
            menu.setActiveAction(action)
            QTest.keyClick(menu, Qt.Key_Return)
        except Exception as exc:
            errors.append(exc)
            if menu:
                menu.close()
    QTimer.singleShot(30, copy_from_menu)
    assert window.share_button.isEnabled() and window.share_button.isVisible()
    QTest.mouseClick(window.share_button, Qt.LeftButton)
    assert not errors, errors
    idle(application, window)
    text_rows = list(csv.reader(io.StringIO(application.clipboard().text()), delimiter="\t"))
    assert tuple(row[0] for row in text_rows[1:]) == expected, window.log_view.toPlainText()
    window.copy_result("html")
    idle(application, window)
    mime = application.clipboard().mimeData()
    assert mime.hasHtml() and mime.hasText()
    assert mime.html().count("<tr>") == 4

    modal(application, lambda: QTest.mouseClick(window.save_table_button, Qt.LeftButton), SaveMemoryDialog,
          lambda dialog: dialog.name_edit.setText("cpu_hot"))
    idle(application, window)
    memory_items = [window.schema_tree.topLevelItem(i) for i in range(window.schema_tree.topLevelItemCount())
                    if window.schema_tree.topLevelItem(i).data(0, Qt.UserRole) == "memory.cpu_hot"]
    assert len(memory_items) == 1 and memory_items[0].text(0) == "메모리"
    assert memory_items[0].text(2) != "—"
    saved = query(application, window, "SELECT * FROM memory.cpu_hot")
    assert saved.current_result().rows == second.current_result().rows
    assert window.result_tabs.tabText(2) == "cpu_hot"

    # The chooser gets the definition from the real task page's persisted repository.
    program = "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); print('task-rows:'+','.join(r[0] for r in d['rows']))"
    task = TaskDefinition("inspect-cpu", "CPU 결과 확인", sys.executable, ("-c", program, "{input}"),
                          None, {}, TaskInputFormat.JSON, ",", ExecutionPolicy(5, 1024 * 1024))
    window.task_service.save_task(task)
    window.tasks_page.refresh()
    modal(application, lambda: QTest.mouseClick(window.task_button, Qt.LeftButton), ChooseTaskDialog,
          lambda dialog: dialog.task_combo.setCurrentIndex(dialog.task_combo.findData(task.id)))
    idle(application, window)
    assert "task-rows:" + ",".join(expected) in window.log_view.toPlainText()
    assert "작업 완료" in window.status_label.text()
    capture(application, window, "workspace-result-tabs")

    # Refreshing command data preserves snapshots and each tab's filters.
    QTest.mouseClick(window.collect_button, Qt.LeftButton)
    idle(application, window)
    assert window.result_tabs.count() == 3 and len(first.current_result().rows) == 5
    assert tuple(row[0] for row in second.current_result().rows) == expected
    close = window.result_tabs.tabBar().tabButton(0, QTabBar.RightSide)
    QTest.mouseClick(close, Qt.LeftButton)
    idle(application, window)
    assert window.result_tabs.count() == 2
    assert window.result_tabs.current_pane() is saved
    query(application, window, "SELECT * FROM memory.cpu_hot")
    assert window.result_tabs.tabText(2) == "cpu_hot (2)"


@pytest.mark.parametrize("format", list(ExportFormat))
def test_export_active_filtered_order_from_dialog(application, window, tmp_path, format):
    pane = query(application, window, "SELECT n, '서울;' || n AS label FROM range(620) AS t(n)")
    pane.open_filter(0)
    popup = pane.filter_popup
    popup.operator_combo.setCurrentIndex(popup.operator_combo.findData(FilterOperator.GREATER_EQUAL.value))
    popup.lower_edit.setText("2")
    QTest.mouseClick(popup.apply_button, Qt.LeftButton)
    pane.open_filter(0)
    QTest.mouseClick(pane.filter_popup.descending_button, Qt.LeftButton)
    output = tmp_path / f"table.{format.value}"
    def fill(dialog):
        dialog.path_edit.setText(str(output))
        if format is ExportFormat.CSV:
            dialog.delimiter_edit.setText(";")
    modal(application, lambda: window.export_result(format), ExportResultDialog, fill)
    idle(application, window)
    assert output.is_file(), window.log_view.toPlainText()
    if format is ExportFormat.CSV:
        with output.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.reader(stream, delimiter=";"))
        assert rows[0] == ["n", "label"] and len(rows) == 619
        assert rows[1] == ["619", "서울;619"] and rows[-1][0] == "2"
    elif format is ExportFormat.JSON:
        rows = json.loads(output.read_text(encoding="utf-8"))
        assert len(rows) == 618 and rows[0] == {"n": 619, "label": "서울;619"}
        assert rows[-1]["n"] == 2
    else:
        with duckdb.connect(str(output), read_only=True) as connection:
            rows = connection.execute("SELECT * FROM result").fetchall()
        assert len(rows) == 618 and rows[0] == (619, "서울;619") and rows[-1][0] == 2


def test_failed_query_and_missing_task_leave_tabs_usable(application, window):
    pane = query(application, window, "SELECT 42 AS answer")
    window.sql_editor.setPlainText("DELETE FROM missing")
    QTest.mouseClick(window.query_button, Qt.LeftButton)
    idle(application, window)
    assert window.result_tabs.count() == 1 and window.result_tabs.current_pane() is pane
    assert window.log_dialog.isVisible()
    window.log_dialog.close()
    QTest.mouseClick(window.task_button, Qt.LeftButton)
    assert window.pages.currentIndex() == PAGE_TASKS and "먼저 등록" in window.status_label.text()
    window.show_page(PAGE_SQL)
    QTest.mouseClick(window.result_tabs.tabBar().tabButton(0, QTabBar.RightSide), Qt.LeftButton)
    idle(application, window)
    assert window.result_tabs.count() == 0 and window.result_empty.isVisible()
    assert not window.share_button.isEnabled() and not window.save_table_button.isEnabled()


def test_task_repository_error_is_reported_without_losing_result(application, window, monkeypatch):
    pane = query(application, window, "SELECT 1")
    def unavailable():
        raise CdbError(ErrorCode.INTERNAL, "task metadata unavailable")
    monkeypatch.setattr(window.task_service, "list_tasks", unavailable)
    QTest.mouseClick(window.task_button, Qt.LeftButton)
    assert window.log_dialog.isVisible() and "task metadata unavailable" in window.log_view.toPlainText()
    assert window.result_tabs.current_pane() is pane and window.query_button.isEnabled()


def test_export_invalid_path_and_delimiter_keep_dialog_open(application, window, tmp_path):
    dialog = ExportResultDialog(ExportFormat.CSV, "result", tmp_path, window)
    dialog.show()
    dialog.path_edit.setText("invalid\0path")
    QTest.mouseClick(dialog.submit_button, Qt.LeftButton)
    assert dialog.isVisible() and "파일 경로" in dialog.feedback.text()
    dialog.path_edit.setText(str(tmp_path / "result.csv"))
    dialog.delimiter_edit.setText("too long")
    QTest.mouseClick(dialog.submit_button, Qt.LeftButton)
    assert dialog.isVisible() and dialog.feedback.text()
    dialog.close()
