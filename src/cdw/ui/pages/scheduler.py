"""Independent host server, scheduled workflows and their persisted history."""

from qtpy.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from qtpy.QtWidgets import QDialog, QHeaderView, QTableWidgetItem

from cdw.domain.contracts import CdwError
from cdw.i18n import tr
from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import ButtonVariant, TextRole
from cdw.ui.pages.base import BasePage, PageId, PageSpec
from cdw.ui.schedule_dialog import ScheduleDialog
from cdw.ui.schedule_presentation import SchedulePresentation
from cdw.ui.widgets import CdwButton, CdwLabel, CdwMessageBox, CdwSection, CdwSectionGroup, CdwTableWidget


class _ServerActionSignals(QObject):
    completed = Signal(object, object)


class _ServerAction(QRunnable):
    """Server lifecycle may wait for another process; never block the GUI thread."""

    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = _ServerActionSignals()

    @Slot()
    def run(self):
        try:
            result = self.operation()
        except Exception as exc:
            self.signals.completed.emit(None, exc)
        else:
            self.signals.completed.emit(result, None)


class SchedulerPage(BasePage):
    REFRESH_INTERVAL_MS = 2500
    HISTORY_LIMIT = 100
    ID_ROLE = Qt.UserRole
    HEADERS = (tr("common.name"), tr("schedule.ui.preset"), tr("schedule.ui.task"),
               tr("schedule.ui.trigger"), tr("schedule.ui.next"), tr("schedule.ui.state"), tr("schedule.ui.last"))
    HISTORY_HEADERS = (tr("schedule.ui.history_time"), tr("schedule.ui.history_workflow"),
                       tr("schedule.ui.history_result"), tr("schedule.ui.history_detail"))
    SPEC = PageSpec(PageId.SCHEDULER, tr("pages.scheduler.title"), tr("pages.scheduler.title"),
                    tr("schedule.ui.subtitle"), IconName.SCHEDULE)

    def __init__(self, service, application_service, task_service, parent=None, *, theme=None):
        self.service = service
        self.application_service = application_service
        self.task_service = task_service
        self._entries = []
        self._history = []
        self._online = False
        self._refreshing = False
        self._refresh_action = None
        self._server_action = None
        super().__init__(parent, theme=theme)
        self.build_content()
        self._timer = QTimer(self)
        self._timer.setInterval(self.REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)

    def build_content(self):
        sections = CdwSectionGroup(theme=self.theme)
        server = CdwSection(tr("schedule.ui.server"), theme=self.theme)
        self.start_button = CdwButton(tr("schedule.ui.server_start"), icon=IconName.PLAY, theme=self.theme)
        self.stop_button = CdwButton(tr("schedule.ui.server_stop"), icon=IconName.STOP, theme=self.theme)
        self.refresh_button = CdwButton(tr("schedule.ui.refresh"), icon=IconName.REFRESH, theme=self.theme)
        for button in (self.start_button, self.stop_button, self.refresh_button):
            server.header.addWidget(button)
        self.server_state = CdwLabel(tr("schedule.ui.server_stopped"), role=TextRole.HEADING, theme=self.theme)
        self.server_details = CdwLabel(role=TextRole.MUTED, theme=self.theme)
        self.server_details.setWordWrap(True)
        server.content.addWidget(self.server_state)
        server.content.addWidget(self.server_details)
        help_label = CdwLabel(tr("schedule.ui.server_help"), role=TextRole.MUTED, theme=self.theme)
        help_label.setWordWrap(True)
        server.content.addWidget(help_label)
        sections.add_section(server)

        schedules = CdwSection(tr("schedule.ui.schedules"), theme=self.theme)
        self.new_button = CdwButton(tr("schedule.ui.add"), icon=IconName.ADD, variant=ButtonVariant.PRIMARY, theme=self.theme)
        self.edit_button = CdwButton(tr("common.edit"), icon=IconName.EDIT, theme=self.theme)
        self.pause_button = CdwButton(tr("schedule.ui.pause"), icon=IconName.STOP, theme=self.theme)
        self.run_button = CdwButton(tr("schedule.ui.run_now"), icon=IconName.PLAY, theme=self.theme)
        self.delete_button = CdwButton(tr("common.delete"), icon=IconName.DELETE, theme=self.theme)
        for button in (self.new_button, self.edit_button, self.pause_button, self.run_button, self.delete_button):
            schedules.header.addWidget(button)
        self.table = CdwTableWidget(0, len(self.HEADERS), theme=self.theme)
        self.table.setObjectName("schedules_table")
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.setSelectionBehavior(CdwTableWidget.SelectRows)
        self.table.setSelectionMode(CdwTableWidget.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        schedules.content.addWidget(self.table, 1)
        self.summary = CdwLabel(tr("schedule.ui.empty"), role=TextRole.MUTED, theme=self.theme)
        self.summary.setWordWrap(True)
        schedules.content.addWidget(self.summary)
        sections.add_section(schedules, 1)

        history = CdwSection(tr("schedule.ui.history"), theme=self.theme)
        self.history_table = CdwTableWidget(0, len(self.HISTORY_HEADERS), theme=self.theme)
        self.history_table.setObjectName("workflow_history")
        self.history_table.setHorizontalHeaderLabels(self.HISTORY_HEADERS)
        self.history_table.setSelectionBehavior(CdwTableWidget.SelectRows)
        self.history_table.setSelectionMode(CdwTableWidget.SingleSelection)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.horizontalHeader().setStretchLastSection(True)
        history.content.addWidget(self.history_table, 1)
        self.history_hint = CdwLabel(tr("schedule.ui.history_empty"), role=TextRole.MUTED, theme=self.theme)
        history.content.addWidget(self.history_hint)
        sections.add_section(history, 1)
        self.content.addWidget(sections, 1)
        self.feedback = CdwLabel(role=TextRole.ERROR, theme=self.theme)
        self.feedback.setWordWrap(True)
        self.content.addWidget(self.feedback)

        self.start_button.clicked.connect(self.start_server)
        self.stop_button.clicked.connect(self.stop_server)
        self.refresh_button.clicked.connect(self.refresh)
        self.new_button.clicked.connect(self.create_schedule)
        self.edit_button.clicked.connect(self.edit_schedule)
        self.pause_button.clicked.connect(self.toggle_schedule)
        self.run_button.clicked.connect(self.run_now)
        self.delete_button.clicked.connect(self.delete_schedule)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_schedule())
        self.history_table.cellDoubleClicked.connect(self._show_run)
        self._selection_changed()

    def on_enter(self):
        self.refresh()
        self._timer.start()

    def on_leave(self):
        self._timer.stop()

    @property
    def selected_schedule(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        identity = item.data(self.ID_ROLE) if item is not None else None
        return next((entry for entry in self._entries if entry.definition.id == identity), None)

    def _selection_changed(self):
        entry = self.selected_schedule
        available = self._server_action is None
        self.start_button.setEnabled(available and not self._online)
        self.stop_button.setEnabled(available and self._online)
        self.refresh_button.setEnabled(available)
        self.new_button.setEnabled(available and self._online)
        for button in (self.edit_button, self.pause_button, self.run_button, self.delete_button):
            button.setEnabled(available and self._online and entry is not None)
        self.pause_button.setText(tr("schedule.ui.pause") if entry is None or entry.definition.enabled else tr("schedule.ui.resume"))

    def _failure(self, exc):
        detail = f"{exc}\n{exc.details}" if isinstance(exc, CdwError) else str(exc)
        self.feedback.setText(tr("schedule.ui.failed").format(details=detail.strip()))

    def refresh(self):
        if self._refreshing or self._server_action is not None:
            return
        self._refreshing = True
        service, application, tasks = self.service, self.application_service, self.task_service
        limit = self.HISTORY_LIMIT
        action = _ServerAction(lambda: SchedulerPage._load_snapshot(service, application, tasks, limit))
        action.signals.completed.connect(self._apply_snapshot, Qt.QueuedConnection)
        self._refresh_action = action
        QThreadPool.globalInstance().start(action)

    @staticmethod
    def _load_snapshot(service, application_service, task_service, limit):
        status = service.server_status()
        if not status.get("running"):
            return status, None
        # Finish every RPC before touching UI so partial reads never repaint half a page.
        entries = service.list_schedules()
        history = service.list_runs(limit=limit)
        workflows = {item.id: item for item in service.list_workflows()}
        presets = {item.id: item.name for item in application_service.list_presets()}
        tasks = {item.id: item.name for item in task_service.list_tasks()}
        return status, (entries, history, workflows, presets, tasks)

    @Slot(object, object)
    def _apply_snapshot(self, snapshot, error):
        self._refresh_action = None
        self._refreshing = False
        if error is not None:
            self._online = False
            self.server_state.setText(tr("schedule.ui.server_stopped"))
            self._failure(error)
            self._selection_changed()
            return
        selected_id = self.selected_schedule.definition.id if self.selected_schedule else None
        try:
            status, data = snapshot
            self._online = bool(status.get("running"))
            self.server_state.setText(tr("schedule.ui.server_running") if self._online else tr("schedule.ui.server_stopped"))
            self.server_details.setText(tr("schedule.ui.server_details").format(
                pid=status.get("pid") or SchedulePresentation.EMPTY, active=status.get("active_runs", 0),
                directory=status.get("data_directory", SchedulePresentation.EMPTY)))
            if not self._online:
                return
            self._entries, self._history, workflows, presets, tasks = data
            self.table.blockSignals(True)
            self.table.setRowCount(len(self._entries))
            for row, entry in enumerate(self._entries):
                definition = entry.definition
                workflow = workflows.get(definition.workflow_id)
                state = (tr("schedule.ui.enabled") if entry.next_run_at else tr("schedule.ui.completed")) if definition.enabled else tr("schedule.ui.paused")
                values = (definition.name,
                    presets.get(workflow.preset_id, SchedulePresentation.EMPTY) if workflow else SchedulePresentation.EMPTY,
                    tasks.get(workflow.task_id, SchedulePresentation.EMPTY) if workflow and workflow.task_id else tr("schedule.ui.collect_only"),
                    SchedulePresentation.trigger(definition.trigger),
                    SchedulePresentation.timestamp(entry.next_run_at, definition.trigger.timezone), state,
                    SchedulePresentation.state(entry.last_run.state) if entry.last_run else SchedulePresentation.EMPTY)
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(self.ID_ROLE, definition.id)
                    item.setToolTip(value)
                    self.table.setItem(row, column, item)
                if definition.id == selected_id:
                    self.table.selectRow(row)
            for column in range(1, len(self.HEADERS)):
                self.table.resizeColumnToContents(column)
            self.summary.setText(tr("schedule.ui.count").format(count=len(self._entries)) if self._entries else tr("schedule.ui.empty"))
            self.history_table.setRowCount(len(self._history))
            for row, run in enumerate(self._history):
                values = (SchedulePresentation.timestamp(run.scheduled_at), run.workflow_name,
                          SchedulePresentation.state(run.state), run.details)
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value)
                    self.history_table.setItem(row, column, item)
            for column in range(len(self.HISTORY_HEADERS) - 1):
                self.history_table.resizeColumnToContents(column)
            self.history_hint.setVisible(not self._history)
            self.feedback.clear()
        except (CdwError, ValueError, OSError) as exc:
            self._online = False
            self._failure(exc)
        finally:
            self.table.blockSignals(False)
            self._refreshing = False
            self._selection_changed()

    def start_server(self):
        self._launch_server_action(self.service.start_server, tr("schedule.ui.server_starting"))

    def stop_server(self):
        if CdwMessageBox.question(self, tr("schedule.ui.stop_title"), tr("schedule.ui.stop_question")) != CdwMessageBox.Yes:
            return
        self._launch_server_action(self.service.stop_server, tr("schedule.ui.server_stopping"))

    def _launch_server_action(self, operation, status):
        if self._server_action is not None:
            return
        action = _ServerAction(operation)
        action.signals.completed.connect(self._server_action_finished, Qt.QueuedConnection)
        self._server_action = action
        self.server_state.setText(status)
        self.feedback.clear()
        self._selection_changed()
        QThreadPool.globalInstance().start(action)

    @Slot(object, object)
    def _server_action_finished(self, _result, error):
        self._server_action = None
        self.refresh()
        if error is not None:
            self._failure(error)

    def _edit(self, entry=None):
        if not self._online:
            return
        self._timer.stop()
        try:
            dialog = ScheduleDialog(self.service, self.application_service, self.task_service, entry, self, theme=self.theme)
            if dialog.exec() == QDialog.Accepted:
                self.refresh()
            dialog.deleteLater()
        except (CdwError, ValueError, OSError) as exc:
            self._failure(exc)
        finally:
            if self.isVisible():
                self._timer.start()

    def create_schedule(self):
        self._edit()

    def edit_schedule(self):
        if self.selected_schedule:
            self._edit(self.selected_schedule)

    def toggle_schedule(self):
        entry = self.selected_schedule
        if entry is None:
            return
        try:
            self.service.set_schedule_enabled(entry.definition.id, not entry.definition.enabled)
            self.refresh()
        except (CdwError, ValueError, OSError) as exc:
            self._failure(exc)

    def run_now(self):
        entry = self.selected_schedule
        if entry is None:
            return
        try:
            run_id = self.service.run_workflow(entry.definition.workflow_id)
            self.log_requested.emit(tr("schedule.ui.run_requested").format(id=run_id))
            self.refresh()
        except (CdwError, ValueError, OSError) as exc:
            self._failure(exc)

    def delete_schedule(self):
        entry = self.selected_schedule
        if entry is None:
            return
        if CdwMessageBox.question(self, tr("schedule.ui.delete_title"), tr("schedule.ui.delete_question").format(name=entry.definition.name)) != CdwMessageBox.Yes:
            return
        try:
            self.service.delete_schedule(entry.definition.id, delete_unused_workflow=True)
            self.refresh()
        except (CdwError, ValueError, OSError) as exc:
            self._failure(exc)

    def _show_run(self, row, _column):
        if row < 0 or row >= len(self._history):
            return
        run = self._history[row]
        dialog = CdwMessageBox(self, theme=self.theme)
        dialog.setWindowTitle(tr("schedule.ui.history_title"))
        dialog.setTextFormat(Qt.PlainText)
        dialog.setText(tr("schedule.ui.history_body").format(
            id=run.id, collection=run.collection_run_id or SchedulePresentation.EMPTY,
            scheduled=SchedulePresentation.timestamp(run.scheduled_at), started=SchedulePresentation.timestamp(run.started_at),
            finished=SchedulePresentation.timestamp(run.finished_at), details=run.details, stdout=run.stdout, stderr=run.stderr))
        dialog.exec()
        dialog.deleteLater()
