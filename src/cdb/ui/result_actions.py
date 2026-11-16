"""Dialogs for choosing how the active result table is used."""

import re
from pathlib import Path

from cdb.domain.contracts import TABLE_NAME_PATTERN, VALIDATION_MESSAGES
from cdb.domain.workspace import DEFAULT_EXPORT_DELIMITER, ExportFormat, validate_delimiter
from cdb.ui.design.icons import IconName
from cdb.ui.design.spec import LayoutRole, TextRole
from cdb.ui.widgets import (
    CdbActionLineEdit, CdbComboBox, CdbDialog, CdbDialogButtonBox, CdbFileDialog,
    CdbFormLayout, CdbLabel, CdbLineEdit, CdbMessageBox, CdbVBoxLayout,
)

EXPORT_FILTERS = {
    ExportFormat.CSV: "CSV (*.csv)", ExportFormat.JSON: "JSON (*.json)",
    ExportFormat.DUCKDB: "DuckDB (*.duckdb)",
}
EXPORT_EXTENSIONS = {ExportFormat.CSV: ".csv", ExportFormat.JSON: ".json", ExportFormat.DUCKDB: ".duckdb"}
FILENAME_UNSAFE = re.compile(r"[^a-zA-Z0-9_]+")
DEFAULT_RESULT_NAME = "query_result"
TEXT = {
    "export_title": "조회 결과 내보내기", "file": "저장 파일", "choose": "찾기",
    "delimiter": "CSV 구분자", "export": "내보내기", "cancel": "취소",
    "delimiter_help": "탭 구분자는 \\t로 입력하세요.",
    "scope": "현재 탭의 필터·정렬을 적용한 전체 행을 사용합니다. 선택한 셀만 내보내지는 않습니다.",
    "path_required": "저장할 파일 경로를 입력해 주세요.",
    "path_invalid": "파일 경로를 확인해 주세요: {details}",
    "replace_title": "파일 덮어쓰기", "replace_body": "다음 파일을 덮어쓸까요?\n{path}",
    "save_title": "메모리 테이블 저장", "name": "테이블 이름", "save": "저장",
    "memory_help": "memory.<이름>으로 SQL에서 조회할 수 있습니다. 앱을 종료하면 사라집니다.\n같은 이름의 기존 테이블은 덮어쓰지 않습니다.",
    "task_title": "조회 결과로 작업 실행", "task": "작업", "run": "실행",
    "task_help": "현재 탭의 필터·정렬 결과를 등록된 작업의 입력 파일로 전달합니다.",
}


def suggested_name(title):
    name = FILENAME_UNSAFE.sub("_", title).strip("_").lower()
    if not TABLE_NAME_PATTERN.fullmatch(name):
        name = DEFAULT_RESULT_NAME
    return name


class ResultActionDialog(CdbDialog):
    def __init__(self, title, parent=None, *, theme=None):
        super().__init__(parent, theme=theme)
        self.setWindowTitle(title)
        self.resize(self.theme.current.metric("dialog_width"), self.theme.current.metric("result_dialog_height"))
        self.content = CdbVBoxLayout(self, role=LayoutRole.DIALOG, theme=self.theme)
        self.form = CdbFormLayout(role=LayoutRole.ROW, theme=self.theme)
        self.content.addLayout(self.form)
        self.feedback = CdbLabel(role=TextRole.ERROR, theme=self.theme)
        self.feedback.setWordWrap(True)

    def add_help(self, text):
        label = CdbLabel(text, role=TextRole.MUTED, theme=self.theme)
        label.setWordWrap(True)
        self.content.addWidget(label)

    def finish(self, action, callback):
        self.content.addWidget(self.feedback)
        self.content.addStretch()
        buttons = CdbDialogButtonBox(theme=self.theme)
        self.submit_button = buttons.add_action(action, CdbDialogButtonBox.AcceptRole, icon=IconName.CHECK, primary=True)
        buttons.add_action(TEXT["cancel"], CdbDialogButtonBox.RejectRole, icon=IconName.CLOSE)
        buttons.accepted.connect(callback)
        buttons.rejected.connect(self.reject)
        self.content.addWidget(buttons)


class ExportResultDialog(ResultActionDialog):
    def __init__(self, format, title, directory, parent=None, *, theme=None):
        super().__init__(TEXT["export_title"], parent, theme=theme)
        self.format = ExportFormat(format)
        self.path_edit = CdbActionLineEdit(str(Path(directory) / (suggested_name(title) + EXPORT_EXTENSIONS[self.format])),
                                           action_text=TEXT["choose"], action_icon=IconName.FOLDER, theme=self.theme)
        self.path_edit.action_button.clicked.connect(self._browse)
        self.form.addRow(TEXT["file"], self.path_edit)
        self.delimiter_edit = CdbLineEdit(DEFAULT_EXPORT_DELIMITER, self, theme=self.theme)
        self.delimiter_edit.setToolTip(TEXT["delimiter_help"])
        if self.format is ExportFormat.CSV:
            self.form.addRow(TEXT["delimiter"], self.delimiter_edit)
        else:
            self.delimiter_edit.hide()
        self.add_help(TEXT["scope"])
        self.path = None
        self.delimiter = DEFAULT_EXPORT_DELIMITER
        self.finish(TEXT["export"], self._accept)

    def _browse(self):
        path, _ = CdbFileDialog.getSaveFileName(self, TEXT["export_title"], self.path_edit.text(), EXPORT_FILTERS[self.format])
        if path:
            self.path_edit.setText(path)

    def _accept(self):
        raw = self.path_edit.text().strip()
        if not raw:
            self.feedback.setText(TEXT["path_required"])
            return
        delimiter = self.delimiter_edit.text()
        delimiter = "\t" if delimiter == r"\t" else delimiter
        if self.format is ExportFormat.CSV:
            try:
                validate_delimiter(delimiter)
            except ValueError as exc:
                self.feedback.setText(str(exc))
                return
        try:
            path = Path(raw).expanduser().resolve()
            if not path.suffix:
                path = path.with_suffix(EXPORT_EXTENSIONS[self.format])
        except (ValueError, OSError, RuntimeError) as exc:
            self.feedback.setText(TEXT["path_invalid"].format(details=exc))
            return
        if path.exists() and CdbMessageBox.question(self, TEXT["replace_title"], TEXT["replace_body"].format(path=path)) != CdbMessageBox.Yes:
            return
        self.path, self.delimiter = path, delimiter
        self.accept()


class SaveMemoryDialog(ResultActionDialog):
    def __init__(self, title, parent=None, *, theme=None):
        super().__init__(TEXT["save_title"], parent, theme=theme)
        self.name_edit = CdbLineEdit(suggested_name(title), theme=self.theme)
        self.form.addRow(TEXT["name"], self.name_edit)
        self.add_help(TEXT["memory_help"])
        self.add_help(TEXT["scope"])
        self.table_name = ""
        self.finish(TEXT["save"], self._accept)

    def _accept(self):
        value = self.name_edit.text().strip()
        if not TABLE_NAME_PATTERN.fullmatch(value):
            self.feedback.setText(VALIDATION_MESSAGES["table"])
            return
        self.table_name = value
        self.accept()


class ChooseTaskDialog(ResultActionDialog):
    def __init__(self, tasks, parent=None, *, theme=None):
        super().__init__(TEXT["task_title"], parent, theme=theme)
        self.task_combo = CdbComboBox(theme=self.theme)
        for task in tasks:
            self.task_combo.addItem(task.name, task.id)
        self.form.addRow(TEXT["task"], self.task_combo)
        self.add_help(TEXT["task_help"])
        self.finish(TEXT["run"], self.accept)

    @property
    def task_id(self):
        return self.task_combo.currentData()
