"""Exercise actual registration/collection/query widgets, not mocked handlers."""

import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from qtpy.QtCore import QPoint, Qt, QTimer
from qtpy.QtGui import QFontDatabase
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication

from cdw.application.service import ApplicationService
from cdw.config import build_config
from cdw.domain.contracts import ExecutionPolicy, OutputFormat, StorageMode
from cdw.server.client import RemoteApplicationService
from cdw.ui.main_window import MainWindow
from cdw.ui.pages.base import PageId
from cdw.ui.log_dialog import LogDialog
from cdw.ui.preset_dialog import PresetDialog
from cdw.ui.widgets import CdwFileDialog
from helpers import make_preset, stop_server


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    # The Windows offscreen plugin does not enumerate system fonts.
    if os.name == "nt" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        for filename in ("malgun.ttf", "malgunbd.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
    yield app


def wait_idle(application, window):
    deadline = time.monotonic() + 15
    while window.controller.busy and time.monotonic() < deadline:
        application.processEvents()
        QTest.qWait(10)
    application.processEvents()
    assert not window.controller.busy, window.log_bar.message_label.text()


@pytest.fixture
def remote_service(tmp_path):
    service = RemoteApplicationService(build_config(tmp_path))
    try:
        yield service
    finally:
        service.close()
        stop_server(service.config)


def test_edit_preserves_exact_execution_limits(application, tmp_path):
    service = ApplicationService(build_config(tmp_path))
    preset = service.save_preset(replace(make_preset(service.config), execution=ExecutionPolicy(0.015, 1025)))
    try:
        dialog = PresetDialog(service, preset)
        dialog.show()
        QTest.mouseClick(dialog.save_button, Qt.LeftButton)
        assert dialog.saved_preset == preset
        assert service.get_preset(preset.id).execution == ExecutionPolicy(0.015, 1025)
        dialog.close()
    finally:
        service.close()


def test_registration_previews_real_output_and_saves_the_edited_environment(application, tmp_path, monkeypatch):
    monkeypatch.setenv("CDW_REMOVE_IN_DIALOG", "removed before collection")
    service = ApplicationService(build_config(tmp_path))
    dialog = PresetDialog(service)
    dialog.show()
    try:
        dialog.name_edit.setText("CSV collection")
        dialog.command_editor.executable_edit.setText(sys.executable)
        dialog.command_editor.arguments_edit.setPlainText("-c\nprint('host,count\\nalpha,2\\nbeta,3')")
        dialog.command_editor.cwd_edit.clear()
        dialog.table_edit.setText("parsed_commands")
        environment = dialog.environment_editor
        removed = next(row for row in range(environment.table.rowCount())
                       if environment.table.item(row, environment.NAME_COLUMN).text() == "CDW_REMOVE_IN_DIALOG")
        dialog.tabs.setCurrentIndex(1)
        application.processEvents()
        environment.table.selectRow(removed)
        QTest.mouseClick(environment.remove_button, Qt.LeftButton)
        assert "CDW_REMOVE_IN_DIALOG" not in environment.environment()
        assert dialog.storage_combo.currentData() == StorageMode.MEMORY
        QTest.mouseClick(dialog.preview_button, Qt.LeftButton)
        deadline = time.monotonic() + 10
        while dialog._preview_busy and time.monotonic() < deadline:
            application.processEvents()
            QTest.qWait(10)
        application.processEvents()
        assert not dialog._preview_busy
        assert dialog.preview_table.rowCount() == 2, dialog.feedback.text()
        assert dialog.preview_table.horizontalHeaderItem(0).text() == "host"
        assert dialog.raw_output.toPlainText() == "host,count\nalpha,2\nbeta,3\n"
        assert service.list_presets() == [] and service.list_runs() == []
        QTest.mouseClick(dialog.save_button, Qt.LeftButton)
        preset = dialog.saved_preset
        assert preset is not None, dialog.feedback.text()
        assert "CDW_REMOVE_IN_DIALOG" not in preset.environment
        assert service.get_preset(preset.id) == preset
        service.collect(preset.id)
        assert service.query(preset.id, "SELECT * FROM source.parsed_commands").rows == (("alpha", 2), ("beta", 3))
    finally:
        dialog.close()
        application.processEvents()
        service.close()


@pytest.mark.parametrize("memory", [False, True])
def test_user_registers_collects_and_queries(application, remote_service, memory):
    window = MainWindow(remote_service)
    window.show()
    wait_idle(application, window)
    window.show_page(PageId.SQL)
    failures = []

    def register():
        dialog = application.activeModalWidget()
        try:
            assert isinstance(dialog, PresetDialog)
            preset = make_preset(dialog.service.config, memory=memory)
            dialog.name_edit.setText(preset.name)
            dialog.command_editor.executable_edit.setText(preset.executable)
            dialog.command_editor.arguments_edit.setPlainText("\n".join(preset.arguments))
            dialog.command_editor.cwd_edit.clear()
            dialog.environment_editor.load_environment(preset.environment)
            dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(OutputFormat.JSON))
            dialog.storage_combo.setCurrentIndex(dialog.storage_combo.findData(preset.storage.mode))
            dialog.table_edit.setText(preset.table_name)
            if os.environ.get("CDW_CAPTURE_DIR") and not memory:
                directory = Path(os.environ["CDW_CAPTURE_DIR"])
                directory.mkdir(parents=True, exist_ok=True)
                dialog.grab().save(str(directory / "preset.png"))
            QTest.mouseClick(dialog.save_button, Qt.LeftButton)
        except Exception as exc:
            failures.append(exc)
            if dialog:
                dialog.reject()

    try:
        QTimer.singleShot(50, register)
        QTest.mouseClick(window.page(PageId.SQL).new_button, Qt.LeftButton)
        assert not failures, failures
        wait_idle(application, window)
        assert window.page(PageId.SQL).selected_preset_id
        selected = window.service.get_preset(window.page(PageId.SQL).selected_preset_id)
        assert window.page(PageId.SQL).preset_combo.currentText() == selected.name
        assert not window.page(PageId.SQL).preset_combo.itemIcon(window.page(PageId.SQL).preset_combo.currentIndex()).isNull()
        assert window.page(PageId.SQL).preset_combo.toolTip() == selected.name
        QTest.mouseClick(window.page(PageId.SQL).collect_button, Qt.LeftButton)
        wait_idle(application, window)
        assert "5행 저장" in window.log_bar.message_label.text(), window.log_dialog.log_view.toPlainText()
        assert window.page(PageId.SQL).schema_tree.topLevelItemCount() == 1
        QTest.mouseClick(window.page(PageId.SQL).query_button, Qt.LeftButton)
        wait_idle(application, window)
        assert window.page(PageId.SQL).result_tabs.current_pane().source_model.rowCount() == 5
        window.page(PageId.SQL).sql_editor.setPlainText("SELECT hostname, cpu_percent, memory_percent, status FROM source.host_inventory ORDER BY cpu_percent DESC")
        QTest.mouseClick(window.page(PageId.SQL).query_button, Qt.LeftButton)
        wait_idle(application, window)
        assert window.page(PageId.SQL).result_tabs.current_pane().current_result().rows[0] == ("rhel-db-01", 67.2, 79.4, "warning")
        capture = os.environ.get("CDW_CAPTURE_DIR")
        if capture and not memory:
            directory = Path(capture)
            directory.mkdir(parents=True, exist_ok=True)
            assert window.grab().save(str(directory / "workspace.png"))
            QTest.mouseClick(window.log_bar, Qt.LeftButton)
            application.processEvents()
            assert window.log_dialog.grab().save(str(directory / "logs.png"))
            window.log_dialog.close()
            window.show_page(PageId.HOME)
            application.processEvents()
            assert window.grab().save(str(directory / "home.png"))
            window.show_page(PageId.SQL)
        # A rejected write is visible and leaves the UI usable.
        window.page(PageId.SQL).sql_editor.setPlainText("DELETE FROM source.host_inventory")
        QTest.mouseClick(window.page(PageId.SQL).query_button, Qt.LeftButton)
        wait_idle(application, window)
        assert "SELECT" in window.log_bar.message_label.text()
        assert window.page(PageId.SQL).query_button.isEnabled()
    finally:
        wait_idle(application, window)
        window.close()
        application.processEvents()


def test_log_bar_opens_live_log_and_preserves_it_when_reopened(application, remote_service):
    window = MainWindow(remote_service)
    window.show()
    wait_idle(application, window)
    bar = window.log_bar
    dialog = window.log_dialog
    try:
        window.log("첫 번째 실행\n상세 기록")
        QTest.mouseClick(bar, Qt.RightButton)
        assert not dialog.isVisible()
        # Both edges, the message, and the otherwise empty bar are clickable.
        for point in (QPoint(1, bar.height() // 2),
                      window.log_bar.message_label.mapTo(bar, window.log_bar.message_label.rect().center()),
                      QPoint(bar.width() - 2, bar.height() // 2)):
            QTest.mouseClick(bar, Qt.LeftButton, pos=point)
            application.processEvents()
            assert dialog.isVisible()
            assert not dialog.isModal()
            assert "첫 번째 실행\n상세 기록" in dialog.log_view.toPlainText()
            QTest.keyClick(dialog, Qt.Key_Escape)
            assert not dialog.isVisible()

        for key in (Qt.Key_Return, Qt.Key_Space):
            bar.setFocus()
            QTest.keyClick(bar, key)
            assert dialog.isVisible()
            QTest.mouseClick(dialog.close_button, Qt.LeftButton)
            assert not dialog.isVisible()

        QTest.mouseClick(bar, Qt.LeftButton)
        window.log("두 번째 실행")
        assert "두 번째 실행" in dialog.log_view.toPlainText()
        assert window.log_bar.message_label.text() == "두 번째 실행"
        QTest.mouseClick(bar, Qt.LeftButton)
        assert window.findChildren(LogDialog) == [dialog]
        assert dialog.log_view.isReadOnly()
    finally:
        wait_idle(application, window)
        window.close()
        application.processEvents()
    assert not dialog.isVisible()


def test_file_action_inside_input_opens_picker_and_preserves_text_on_cancel(application, tmp_path):
    service = ApplicationService(build_config(tmp_path))
    dialog = PresetDialog(service)
    dialog.show()
    dialog.tabs.setCurrentIndex(3)
    dialog.storage_combo.setCurrentIndex(dialog.storage_combo.findData(StorageMode.FILE))
    application.processEvents()
    original = dialog.path_edit.text()
    failures = []
    def cancel_picker():
        picker = application.activeModalWidget()
        try:
            assert isinstance(picker, CdwFileDialog)
        except Exception as exc:
            failures.append(exc)
        finally:
            if picker is not None:
                picker.reject()
    try:
        button = dialog.path_edit.action_button
        assert button.parentWidget() is dialog.path_edit
        assert dialog.path_edit.rect().contains(button.geometry())
        QTimer.singleShot(100, cancel_picker)
        QTest.mouseClick(button, Qt.LeftButton)
        assert not failures
        assert dialog.path_edit.text() == original
        if capture := os.environ.get("CDW_CAPTURE_DIR"):
            directory = Path(capture)
            directory.mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(directory / "cdw-integrated-file-field.png"))
        dialog.storage_combo.setCurrentIndex(dialog.storage_combo.findData(StorageMode.MEMORY))
        assert not button.isEnabled()
        dialog.storage_combo.setCurrentIndex(dialog.storage_combo.findData(StorageMode.FILE))
        assert button.isEnabled()
    finally:
        dialog.close()
        application.processEvents()
        service.close()
