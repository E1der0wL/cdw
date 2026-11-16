"""Task definitions are edited here and invoked from a result tab."""

from __future__ import annotations

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import QDialog, QTableWidgetItem

from cdb.application.tasks import TaskService
from cdb.domain.contracts import CdbError
from cdb.ui.design.icons import IconName
from cdb.ui.design.spec import ButtonVariant, TextRole
from cdb.ui.task_dialog import TaskDialog
from cdb.ui.widgets import CdbButton, CdbLabel, CdbMessageBox, CdbPage, CdbSection, CdbTableWidget

HEADERS = ("이름", "입력", "실행 파일", "제한 시간")
TASK_ID_ROLE = Qt.UserRole
TEXT = {
    "title": "작업", "subtitle": "조회 결과를 입력 파일로 전달하여 로컬 프로그램을 실행합니다.",
    "section": "등록된 작업", "new": "작업 등록", "edit": "편집", "delete": "삭제", "refresh": "새로고침",
    "help": "SQL 결과 탭의 ‘작업’ 버튼에서 실행할 작업을 선택하세요. 필터·정렬이 적용된 테이블 전체를 전달합니다.",
    "empty": "등록된 작업이 없습니다. 실행 파일과 {input} 인자를 등록해 주세요.",
    "count": "{count}개 작업", "seconds": "{seconds:g}초", "delete_title": "작업 삭제",
    "delete_question": "‘{name}’ 작업 설정을 삭제하시겠습니까?", "error": "작업 처리 실패",
}


class TasksPage(CdbPage):
    tasksChanged = Signal()

    def __init__(self, service: TaskService, parent=None, *, theme=None):
        super().__init__(TEXT["title"], TEXT["subtitle"], parent, theme=theme)
        self.service = service
        self._tasks = []
        self._busy = False
        section = CdbSection(TEXT["section"], theme=self.theme)
        self.new_button = CdbButton(TEXT["new"], variant=ButtonVariant.PRIMARY, icon=IconName.ADD, theme=self.theme)
        self.edit_button = CdbButton(TEXT["edit"], icon=IconName.EDIT, theme=self.theme)
        self.delete_button = CdbButton(TEXT["delete"], icon=IconName.DELETE, theme=self.theme)
        self.refresh_button = CdbButton(TEXT["refresh"], icon=IconName.REFRESH, theme=self.theme)
        for button in (self.new_button, self.edit_button, self.delete_button, self.refresh_button):
            section.header.addWidget(button)
        self.table = CdbTableWidget(0, len(HEADERS), theme=self.theme)
        self.table.setObjectName("tasks_table")
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(CdbTableWidget.SelectRows)
        self.table.setSelectionMode(CdbTableWidget.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        section.content.addWidget(self.table, 1)
        self.status_label = CdbLabel(role=TextRole.MUTED, theme=self.theme)
        self.status_label.setWordWrap(True)
        section.content.addWidget(self.status_label)
        hint = CdbLabel(TEXT["help"], role=TextRole.MUTED, theme=self.theme)
        hint.setWordWrap(True)
        section.content.addWidget(hint)
        self.content.addWidget(section, 1)
        self.new_button.clicked.connect(self.create_task)
        self.edit_button.clicked.connect(self.edit_task)
        self.delete_button.clicked.connect(self.delete_task)
        self.refresh_button.clicked.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda *_: self.edit_task())
        self.refresh()

    @property
    def selected_task(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._tasks):
            return None
        item = self.table.item(row, 0)
        task_id = item.data(TASK_ID_ROLE) if item is not None else None
        return next((task for task in self._tasks if task.id == task_id), None)

    def set_busy(self, busy: bool):
        self._busy = busy
        self.new_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self._selection_changed()

    def _selection_changed(self):
        enabled = not self._busy and self.selected_task is not None
        self.edit_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    def refresh(self):
        selected_id = self.selected_task.id if self.selected_task else None
        try:
            self._tasks = self.service.list_tasks()
        except CdbError as exc:
            self.status_label.setText(f"{exc}\n{exc.details}".strip())
            return
        self.table.setRowCount(len(self._tasks))
        for row, task in enumerate(self._tasks):
            values = (task.name, task.input_format.value.upper(), task.executable,
                      TEXT["seconds"].format(seconds=task.execution.timeout_seconds))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(TASK_ID_ROLE, task.id)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
            if task.id == selected_id:
                self.table.selectRow(row)
        self.table.resizeColumnsToContents()
        self.status_label.setText(TEXT["count"].format(count=len(self._tasks)) if self._tasks else TEXT["empty"])
        self._selection_changed()

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
        if CdbMessageBox.question(self, TEXT["delete_title"], TEXT["delete_question"].format(name=task.name)) != CdbMessageBox.Yes:
            return
        try:
            self.service.delete_task(task.id)
        except CdbError as exc:
            CdbMessageBox.warning(self, TEXT["error"], f"{exc}\n{exc.details}".strip())
            return
        self.refresh()
        self.tasksChanged.emit()
