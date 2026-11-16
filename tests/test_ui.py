"""Exercise actual registration/collection/query widgets, not mocked handlers."""

import os
import time
from dataclasses import replace
from pathlib import Path

import pytest
from qtpy.QtCore import QPoint, Qt, QTimer
from qtpy.QtGui import QFontDatabase
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication

from cdb.application.service import ApplicationService
from cdb.application.examples import demo_preset
from cdb.config import build_config
from cdb.domain.contracts import ExecutionPolicy, StorageMode
from cdb.ui.main_window import MainWindow, PAGE_SQL
from cdb.ui.log_dialog import LogDialog
from cdb.ui.preset_dialog import PresetDialog
from cdb.ui.widgets import CdbFileDialog


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
    assert not window.controller.busy, window.status_label.text()


def test_edit_preserves_exact_execution_limits(application, tmp_path):
    service = ApplicationService(build_config(tmp_path))
    preset = service.save_preset(replace(demo_preset(service.config), execution=ExecutionPolicy(0.015, 1025)))
    try:
        dialog = PresetDialog(service, preset)
        dialog.show()
        QTest.mouseClick(dialog.save_button, Qt.LeftButton)
        assert dialog.saved_preset == preset
        assert service.get_preset(preset.id).execution == ExecutionPolicy(0.015, 1025)
        dialog.close()
    finally:
        service.close()


@pytest.mark.parametrize("memory", [False, True])
def test_user_registers_collects_and_queries(application, tmp_path, memory):
    window = MainWindow(ApplicationService(build_config(tmp_path)))
    window.show()
    wait_idle(application, window)
    window.show_page(PAGE_SQL)
    failures = []

    def register():
        dialog = application.activeModalWidget()
        try:
            assert isinstance(dialog, PresetDialog)
            QTest.mouseClick(dialog.demo_button, Qt.LeftButton)
            if os.environ.get("CDB_CAPTURE_DIR") and not memory:
                directory = Path(os.environ["CDB_CAPTURE_DIR"])
                directory.mkdir(parents=True, exist_ok=True)
                dialog.grab().save(str(directory / "preset.png"))
            if memory:
                dialog.storage_combo.setCurrentIndex(dialog.storage_combo.findData(StorageMode.MEMORY))
            QTest.mouseClick(dialog.save_button, Qt.LeftButton)
        except Exception as exc:
            failures.append(exc)
            if dialog:
                dialog.reject()

    try:
        QTimer.singleShot(50, register)
        QTest.mouseClick(window.new_button, Qt.LeftButton)
        assert not failures, failures
        wait_idle(application, window)
        assert window.selected_preset_id
        selected = window.service.get_preset(window.selected_preset_id)
        assert window.preset_combo.currentText() == selected.name
        assert not window.preset_combo.itemIcon(window.preset_combo.currentIndex()).isNull()
        assert window.preset_combo.toolTip() == selected.name
        QTest.mouseClick(window.collect_button, Qt.LeftButton)
        wait_idle(application, window)
        assert "5행 저장" in window.status_label.text(), window.log_view.toPlainText()
        assert window.schema_tree.topLevelItemCount() == 1
        QTest.mouseClick(window.query_button, Qt.LeftButton)
        wait_idle(application, window)
        assert window.result_model.rowCount() == 5
        window.sql_editor.setPlainText("SELECT hostname, cpu_percent, memory_percent, status FROM source.host_inventory ORDER BY cpu_percent DESC")
        QTest.mouseClick(window.query_button, Qt.LeftButton)
        wait_idle(application, window)
        assert window.result_model.result.rows[0] == ("rhel-db-01", 67.2, 79.4, "warning")
        capture = os.environ.get("CDB_CAPTURE_DIR")
        if capture and not memory:
            directory = Path(capture)
            directory.mkdir(parents=True, exist_ok=True)
            assert window.grab().save(str(directory / "workspace.png"))
            QTest.mouseClick(window.log_bar, Qt.LeftButton)
            application.processEvents()
            assert window.log_dialog.grab().save(str(directory / "logs.png"))
            window.log_dialog.close()
            window.show_page(0)
            application.processEvents()
            assert window.grab().save(str(directory / "home.png"))
            window.show_page(PAGE_SQL)
        # A rejected write is visible and leaves the UI usable.
        window.sql_editor.setPlainText("DELETE FROM source.host_inventory")
        QTest.mouseClick(window.query_button, Qt.LeftButton)
        wait_idle(application, window)
        assert "SELECT" in window.status_label.text()
        assert window.query_button.isEnabled()
    finally:
        wait_idle(application, window)
        window.close()
        application.processEvents()


def test_log_bar_opens_live_log_and_preserves_it_when_reopened(application, tmp_path):
    window = MainWindow(ApplicationService(build_config(tmp_path)))
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
                      window.status_label.mapTo(bar, window.status_label.rect().center()),
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
        assert window.status_label.text() == "두 번째 실행"
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
    dialog.tabs.setCurrentIndex(1)
    application.processEvents()
    original = dialog.path_edit.text()
    failures = []
    def cancel_picker():
        picker = application.activeModalWidget()
        try:
            assert isinstance(picker, CdbFileDialog)
        except Exception as exc:
            failures.append(exc)
        finally:
            if picker is not None:
                picker.reject()
    try:
        assert dialog.path_button.parentWidget() is dialog.path_edit
        assert dialog.path_edit.rect().contains(dialog.path_button.geometry())
        QTimer.singleShot(100, cancel_picker)
        QTest.mouseClick(dialog.path_button, Qt.LeftButton)
        assert not failures
        assert dialog.path_edit.text() == original
        if capture := os.environ.get("CDB_CAPTURE_DIR"):
            directory = Path(capture)
            directory.mkdir(parents=True, exist_ok=True)
            assert dialog.grab().save(str(directory / "cdw-integrated-file-field.png"))
        dialog.storage_combo.setCurrentIndex(dialog.storage_combo.findData(StorageMode.MEMORY))
        assert not dialog.path_button.isEnabled()
        dialog.storage_combo.setCurrentIndex(dialog.storage_combo.findData(StorageMode.FILE))
        assert dialog.path_button.isEnabled()
    finally:
        dialog.close()
        application.processEvents()
        service.close()
