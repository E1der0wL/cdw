"""Approved scheduler widgets drive the real detached server through public controls."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import time

import pytest
from qtpy.QtCore import QCoreApplication, QEvent, Qt
from qtpy.QtGui import QFontDatabase
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication, QDialog

from cdw.config import build_config
from cdw.domain.contracts import ExecutionPolicy, OutputFormat, Preset, StorageMode, StorageTarget, WriteMode
from cdw.domain.schedules import TriggerKind, WorkflowRunState
from cdw.domain.tasks import TaskDefinition, TaskInputFormat
from cdw.i18n import tr
from cdw.server.client import RemoteApplicationService
from cdw.ui.design.manager import ThemeManager
from cdw.ui.pages.scheduler import SchedulerPage
from cdw.ui.schedule_dialog import ScheduleDialog
from helpers import stop_server


WAIT_SECONDS = 20
LOCAL_ZONE = timezone(timedelta(hours=9))
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
EXPECTED_ROWS = (("alpha", 3), ("beta", 5))


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    if os.name == "nt" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        for filename in ("malgun.ttf", "malgunbd.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
    return app


@pytest.fixture
def workspace(application, tmp_path):
    config = build_config(tmp_path / "profile")
    service = None
    try:
        service = RemoteApplicationService(config)
        preset = Preset("preset", "UI command collection", sys.executable,
            ("-c", "print('name,value'); print('alpha,3'); print('beta,5')"),
            str(tmp_path), dict(os.environ), OutputFormat.CSV, "utf-8", ",",
            StorageTarget(StorageMode.MEMORY, None), "ui_collected", WriteMode.REPLACE,
            ExecutionPolicy(5, 65536))
        service.save_preset(preset)
        marker = tmp_path / "external-task.txt"
        task = TaskDefinition("task", "UI external process", sys.executable,
            ("-c", "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('executed'); print('done')", str(marker)),
            str(tmp_path), dict(os.environ), TaskInputFormat.NONE, ",", ExecutionPolicy(5, 65536))
        service.tasks.save_task(task)
        yield service, ThemeManager.for_service(service), preset, task, marker
    finally:
        if service is not None:
            service.close()
        stop_server(config)


def wait_for(application, condition):
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        application.processEvents()
        if condition():
            return
        QTest.qWait(10)
    raise AssertionError("The scheduler UI did not reach the expected state")


def dispose(application, widget):
    if isinstance(widget, SchedulerPage):
        widget.on_leave()
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application.processEvents()


def prepare_dialog(application, workspace, kind):
    service, theme, preset, task, _ = workspace
    dialog = ScheduleDialog(service.schedules, service, service.tasks, theme=theme)
    dialog.resize(1000, 900)
    dialog.show()
    dialog.name_edit.setText(f"UI {kind.value} collection")
    dialog.preset_combo.setCurrentIndex(dialog.preset_combo.findData(preset.id))
    dialog.task_combo.setCurrentIndex(dialog.task_combo.findData(task.id))
    dialog.enabled_combo.setCurrentIndex(dialog.enabled_combo.findData(False))
    # Exercise QVariant round-tripping through actual Qt controls.
    dialog.trigger_combo.setCurrentIndex(dialog.trigger_combo.findData(kind.value))
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0)
    local_text = future.astimezone(LOCAL_ZONE).strftime(DATE_FORMAT)
    dialog.anchor_edit.setText(local_text)
    dialog.once_edit.setText(local_text)
    dialog.interval_spin.setValue(7)
    dialog.interval_unit.setCurrentIndex(dialog.interval_unit.findData(60))
    dialog.clock_edit.setText("10:45:00")
    for day, button in enumerate(dialog.weekday_buttons):
        button.setChecked(day in (0, 2, 4))
    application.processEvents()
    return dialog, future


def assert_trigger_fields(dialog, kind):
    for field, expected in ((dialog.period_widget, kind is TriggerKind.INTERVAL),
                            (dialog.anchor_edit, kind is TriggerKind.INTERVAL),
                            (dialog.once_edit, kind is TriggerKind.ONCE),
                            (dialog.clock_edit, kind is TriggerKind.WEEKLY),
                            (dialog.weekdays_widget, kind is TriggerKind.WEEKLY)):
        assert field.isVisible() == expected
        assert dialog.timing_form.labelForField(field).isVisible() == expected


@pytest.mark.parametrize("kind", list(TriggerKind))
def test_dialog_shows_only_relevant_controls_and_registers_exact_disabled_schedule(application, workspace, kind):
    service, theme, preset, task, _ = workspace
    dialog, future = prepare_dialog(application, workspace, kind)
    editor = None
    try:
        assert_trigger_fields(dialog, kind)
        assert dialog.save_button.isEnabled()
        QTest.mouseClick(dialog.save_button, Qt.LeftButton)
        assert dialog.result() == QDialog.Accepted, dialog.feedback.text()
        saved = dialog.saved_schedule
        assert saved is not None and not saved.definition.enabled and saved.next_run_at is None
        persisted = service.schedules.get_schedule(saved.definition.id)
        assert persisted == saved
        workflow = service.schedules.get_workflow(saved.definition.workflow_id)
        assert (workflow.preset_id, workflow.task_id) == (preset.id, task.id)
        trigger = persisted.definition.trigger
        assert trigger.kind is kind and trigger.timezone == "Asia/Seoul"
        if kind is TriggerKind.INTERVAL:
            assert trigger.interval_seconds == 420
            assert datetime.fromisoformat(trigger.anchor_at) == future
        elif kind is TriggerKind.ONCE:
            assert datetime.fromisoformat(trigger.at) == future
        else:
            assert trigger.wall_time == "10:45:00" and trigger.weekdays == (0, 2, 4)
        editor = ScheduleDialog(service.schedules, service, service.tasks, persisted, theme=theme)
        editor.show()
        application.processEvents()
        assert_trigger_fields(editor, kind)
        assert editor.name_edit.text() == persisted.definition.name
        assert not editor.enabled_combo.currentData()
        assert editor.task_combo.currentData() == task.id
    finally:
        if editor is not None:
            dispose(application, editor)
        dispose(application, dialog)


def test_page_refresh_resume_pause_and_run_now_reach_real_history(application, workspace):
    service, theme, preset, _, marker = workspace
    dialog, _ = prepare_dialog(application, workspace, TriggerKind.INTERVAL)
    page = None
    try:
        QTest.mouseClick(dialog.save_button, Qt.LeftButton)
        assert dialog.result() == QDialog.Accepted, dialog.feedback.text()
        schedule_id = dialog.saved_schedule.definition.id
        page = SchedulerPage(service.schedules, service, service.tasks, theme=theme)
        page.resize(1240, 900)
        page.show()
        page.on_enter()
        wait_for(application, lambda: page.table.rowCount() == 1 and page.stop_button.isEnabled())
        assert page.server_state.text() == tr("schedule.ui.server_running")
        assert not page.start_button.isEnabled() and page.new_button.isEnabled()
        assert page.table.item(0, 0).text() == dialog.saved_schedule.definition.name
        assert not page.grab().isNull()
        page.table.selectRow(0)
        application.processEvents()
        assert page.pause_button.text() == tr("schedule.ui.resume")
        QTest.mouseClick(page.pause_button, Qt.LeftButton)
        wait_for(application, lambda: page.table.item(0, 5).text() == tr("schedule.ui.enabled"))
        assert service.schedules.get_schedule(schedule_id).definition.enabled
        assert page.pause_button.text() == tr("schedule.ui.pause")
        QTest.mouseClick(page.pause_button, Qt.LeftButton)
        wait_for(application, lambda: page.table.item(0, 5).text() == tr("schedule.ui.paused"))
        paused = service.schedules.get_schedule(schedule_id)
        assert not paused.definition.enabled and paused.next_run_at is None
        messages = []
        page.log_requested.connect(messages.append)
        assert page.run_button.isEnabled()
        QTest.mouseClick(page.run_button, Qt.LeftButton)
        wait_for(application, lambda: page.history_table.rowCount() == 1
            and page.history_table.item(0, 2).text() == tr("schedule.ui.succeeded"))
        assert messages and marker.read_text() == "executed"
        run, = service.schedules.list_runs()
        assert run.state is WorkflowRunState.SUCCEEDED
        assert run.schedule_id is None and run.collection_run_id is not None
        assert not service.schedules.get_schedule(schedule_id).definition.enabled
        snapshot = service.query_workspace(preset.id, f"SELECT * FROM {preset.qualified_table} ORDER BY name")
        try:
            assert snapshot.result.rows == EXPECTED_ROWS
        finally:
            service.release_result(snapshot.id)
        assert not page.feedback.text()
        page.on_leave()
        page.close()
        assert service.schedules.server_status()["running"]
    finally:
        if page is not None:
            dispose(application, page)
        dispose(application, dialog)
