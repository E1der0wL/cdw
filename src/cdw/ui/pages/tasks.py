"""External tasks, including direct execution without a table input."""

from __future__ import annotations

from cdw.i18n import tr
from cdw.ui.pages.base import BasePage, PageId, PageSpec

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import QDialog, QTableWidgetItem

from cdw.application.tasks import TaskService
from cdw.domain.contracts import CdwError, CommandMode
from cdw.domain.tasks import TaskInputFormat
from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import ButtonVariant, TextRole
from cdw.ui.task_dialog import TaskDialog
from cdw.ui.widgets import CdwButton, CdwLabel, CdwMessageBox, CdwSection, CdwTableWidget


class TasksPage(BasePage):

    HEADERS = (tr('common.name'), tr('ui.pages.tasks.headers'), tr('tasks.page.mode_header'),
               tr('tasks.page.program_header'), tr('ui.pages.tasks.headers_3'))
    TASK_ID_ROLE = Qt.UserRole
    INPUT_LABELS = {TaskInputFormat.NONE: tr("tasks.input.none_short"),
                    TaskInputFormat.JSON: tr("tasks.input.json_short"), TaskInputFormat.CSV: tr("tasks.input.csv_short")}
    COMMAND_LABELS = {CommandMode.DIRECT: tr("tasks.page.direct"), CommandMode.SHELL: tr("tasks.page.shell")}
    TEXT = {
        "title": tr('pages.tasks.title'), "subtitle": tr('tasks.page.subtitle'),
        "section": tr('ui.pages.tasks.text.section'), "new": tr('ui.pages.tasks.text.new'), "edit": tr('common.edit'), "delete": tr('common.delete'), "refresh": tr('ui.pages.tasks.text.refresh'),
        "help": tr('tasks.page.help'),
        "empty": tr('tasks.page.empty'),
        "count": tr('ui.pages.tasks.text.count'), "seconds": tr('ui.pages.tasks.text.seconds'), "delete_title": tr('ui.pages.tasks.text.delete_title'),
        "delete_question": tr('ui.pages.tasks.text.delete_question'), "error": tr('ui.pages.tasks.text.error'),
        "run": tr("tasks.page.run"), "run_hint": tr("tasks.page.run_hint"), "running": tr("tasks.page.running"),
    }

    SPEC = PageSpec(PageId.TASKS, TEXT["title"], TEXT["title"], TEXT["subtitle"], IconName.TASKS)
    tasksChanged = Signal()

    def __init__(self, service: TaskService, parent=None, *, controller=None, theme=None):
        self.service = service
        self.controller = controller
        self._tasks = []
        self._busy = False
        super().__init__(parent, theme=theme)
        self.build_content()

    def on_enter(self):
        self.refresh()

    def build_content(self):
        section = CdwSection(TasksPage.TEXT["section"], theme=self.theme)
        self.new_button = CdwButton(TasksPage.TEXT["new"], icon=IconName.ADD, theme=self.theme)
        self.edit_button = CdwButton(TasksPage.TEXT["edit"], icon=IconName.EDIT, theme=self.theme)
        self.delete_button = CdwButton(TasksPage.TEXT["delete"], icon=IconName.DELETE, theme=self.theme)
        self.refresh_button = CdwButton(TasksPage.TEXT["refresh"], icon=IconName.REFRESH, theme=self.theme)
        self.run_button = CdwButton(self.TEXT["run"], variant=ButtonVariant.PRIMARY, icon=IconName.PLAY, theme=self.theme)
        self.run_button.setObjectName("run_task_without_input")
        self.run_button.setToolTip(self.TEXT["run_hint"])
        for button in (self.new_button, self.edit_button, self.delete_button, self.refresh_button, self.run_button):
            section.header.addWidget(button)
        self.table = CdwTableWidget(0, len(TasksPage.HEADERS), theme=self.theme)
        self.table.setObjectName("tasks_table")
        self.table.setHorizontalHeaderLabels(TasksPage.HEADERS)
        self.table.setSelectionBehavior(CdwTableWidget.SelectRows)
        self.table.setSelectionMode(CdwTableWidget.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        section.content.addWidget(self.table, 1)
        self.status_label = CdwLabel(role=TextRole.MUTED, theme=self.theme)
        self.status_label.setWordWrap(True)
        section.content.addWidget(self.status_label)
        hint = CdwLabel(TasksPage.TEXT["help"], role=TextRole.MUTED, theme=self.theme)
        hint.setWordWrap(True)
        section.content.addWidget(hint)
        self.content.addWidget(section, 1)
        self.new_button.clicked.connect(self.create_task)
        self.edit_button.clicked.connect(self.edit_task)
        self.delete_button.clicked.connect(self.delete_task)
        self.refresh_button.clicked.connect(self.refresh)
        self.run_button.clicked.connect(self.run_selected)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_task())
        self.refresh()

    @property
    def selected_task(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._tasks):
            return None
        item = self.table.item(row, 0)
        task_id = item.data(TasksPage.TASK_ID_ROLE) if item is not None else None
        return next((task for task in self._tasks if task.id == task_id), None)

    def set_busy(self, busy: bool):
        self._busy = busy
        self.new_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self._selection_changed()

    def _selection_changed(self):
        task = self.selected_task
        enabled = not self._busy and task is not None
        self.edit_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        self.run_button.setEnabled(enabled and self.controller is not None and not task.requires_input)

    def refresh(self):
        selected_id = self.selected_task.id if self.selected_task else None
        try:
            self._tasks = self.service.list_tasks()
        except CdwError as exc:
            self.status_label.setText(f"{exc}\n{exc.details}".strip())
            return
        self.table.setRowCount(len(self._tasks))
        for row, task in enumerate(self._tasks):
            values = (task.name, self.INPUT_LABELS[task.input_format], self.COMMAND_LABELS[task.command_mode], task.executable,
                      TasksPage.TEXT["seconds"].format(seconds=task.execution.timeout_seconds))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(TasksPage.TASK_ID_ROLE, task.id)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
            if task.id == selected_id:
                self.table.selectRow(row)
        self.table.resizeColumnsToContents()
        self.status_label.setText(TasksPage.TEXT["count"].format(count=len(self._tasks)) if self._tasks else TasksPage.TEXT["empty"])
        self._selection_changed()

    def run_selected(self):
        task = self.selected_task
        if self._busy or self.controller is None or task is None or task.requires_input:
            return
        if self.controller.run_task(task.id, None):
            self.log_requested.emit(self.TEXT["running"].format(name=task.name))

    def create_task(self):
        if self._busy:
            return
        dialog = TaskDialog(self.service, parent=self, theme=self.theme)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.tasksChanged.emit()
        dialog.deleteLater()

    def edit_task(self):
        task = self.selected_task
        if self._busy or task is None:
            return
        dialog = TaskDialog(self.service, task, self, theme=self.theme)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.tasksChanged.emit()
        dialog.deleteLater()

    def delete_task(self):
        task = self.selected_task
        if self._busy or task is None:
            return
        if CdwMessageBox.question(self, TasksPage.TEXT["delete_title"], TasksPage.TEXT["delete_question"].format(name=task.name)) != CdwMessageBox.Yes:
            return
        try:
            self.service.delete_task(task.id)
        except CdwError as exc:
            CdwMessageBox.warning(self, TasksPage.TEXT["error"], f"{exc}\n{exc.details}".strip())
            return
        self.refresh()
        self.tasksChanged.emit()
