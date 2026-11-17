"""Dialogs for choosing how the active result table is used."""

from cdw.i18n import tr

import re
from pathlib import Path

from cdw.domain.contracts import TABLE_NAME_PATTERN, VALIDATION_MESSAGES
from cdw.domain.workspace import DEFAULT_EXPORT_DELIMITER, ExportFormat, validate_delimiter
from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import LayoutRole, TextRole
from cdw.ui.widgets import (
    CdwActionLineEdit, CdwComboBox, CdwDialog, CdwDialogButtonBox, CdwFileDialog,
    CdwFormLayout, CdwLabel, CdwLineEdit, CdwMessageBox, CdwVBoxLayout,
)

FILENAME_UNSAFE = re.compile(r"[^a-zA-Z0-9_]+")
DEFAULT_RESULT_NAME = "query_result"
TEXT = {
    "export_title": tr('ui.result_actions.text.export_title'), "file": tr('ui.result_actions.text.file'), "choose": tr('ui.result_actions.text.choose'),
    "delimiter": tr('ui.preset_dialog.labels.delimiter'), "export": tr('ui.result_actions.text.export'), "cancel": tr('common.cancel'),
    "delimiter_help": tr('ui.result_actions.text.delimiter_help'),
    "scope": tr('ui.result_actions.text.scope'),
    "path_required": tr('ui.result_actions.text.path_required'),
    "path_invalid": tr('ui.result_actions.text.path_invalid'),
    "replace_title": tr('ui.result_actions.text.replace_title'), "replace_body": tr('ui.result_actions.text.replace_body'),
    "save_title": tr('ui.result_actions.text.save_title'), "name": tr('ui.preset_dialog.labels.table'), "save": tr('ui.pages.sql.text.save_table'),
    "memory_help": tr('ui.result_actions.text.memory_help'),
    "task_title": tr('ui.result_actions.text.task_title'), "task": tr('pages.tasks.title'), "run": tr('ui.pages.settings.text.primary'),
    "task_help": tr('ui.result_actions.text.task_help'),
}


def suggested_name(title):
    name = FILENAME_UNSAFE.sub("_", title).strip("_").lower()
    if not TABLE_NAME_PATTERN.fullmatch(name):
        name = DEFAULT_RESULT_NAME
    return name


class ResultActionDialog(CdwDialog):
    def __init__(self, title, parent=None, *, theme=None):
        super().__init__(parent, theme=theme)
        self.setWindowTitle(title)
        self.resize(self.theme.current.metric("dialog_width"), self.theme.current.metric("result_dialog_height"))
        self.content = CdwVBoxLayout(self, role=LayoutRole.DIALOG, theme=self.theme)
        self.form = CdwFormLayout(role=LayoutRole.FORM, theme=self.theme)
        self.content.addLayout(self.form)
        self.feedback = CdwLabel(role=TextRole.ERROR, theme=self.theme)
        self.feedback.setWordWrap(True)

    def add_help(self, text):
        label = CdwLabel(text, role=TextRole.MUTED, theme=self.theme)
        label.setWordWrap(True)
        self.content.addWidget(label)

    def finish(self, action, callback):
        self.content.addWidget(self.feedback)
        self.content.addStretch()
        buttons = CdwDialogButtonBox(theme=self.theme)
        self.submit_button = buttons.add_action(action, CdwDialogButtonBox.AcceptRole, icon=IconName.CHECK, primary=True)
        buttons.add_action(TEXT["cancel"], CdwDialogButtonBox.RejectRole, icon=IconName.CLOSE)
        buttons.accepted.connect(callback)
        buttons.rejected.connect(self.reject)
        self.content.addWidget(buttons)


class ExportResultDialog(ResultActionDialog):

    EXPORT_FILTERS = {
        ExportFormat.CSV: "CSV (*.csv)", ExportFormat.JSON: "JSON (*.json)",
        ExportFormat.DUCKDB: "DuckDB (*.duckdb)",
    }
    EXPORT_EXTENSIONS = {ExportFormat.CSV: ".csv", ExportFormat.JSON: ".json", ExportFormat.DUCKDB: ".duckdb"}

    def __init__(self, format, title, directory, parent=None, *, theme=None):
        super().__init__(TEXT["export_title"], parent, theme=theme)
        self.format = ExportFormat(format)
        self.path_edit = CdwActionLineEdit(str(Path(directory) / (suggested_name(title) + ExportResultDialog.EXPORT_EXTENSIONS[self.format])),
                                           action_text=TEXT["choose"], action_icon=IconName.FOLDER, theme=self.theme)
        self.path_edit.action_button.clicked.connect(self._browse)
        self.form.addRow(TEXT["file"], self.path_edit)
        self.delimiter_edit = CdwLineEdit(DEFAULT_EXPORT_DELIMITER, self, theme=self.theme)
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
        path, _ = CdwFileDialog.getSaveFileName(self, TEXT["export_title"], self.path_edit.text(), ExportResultDialog.EXPORT_FILTERS[self.format])
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
                path = path.with_suffix(ExportResultDialog.EXPORT_EXTENSIONS[self.format])
        except (ValueError, OSError, RuntimeError) as exc:
            self.feedback.setText(TEXT["path_invalid"].format(details=exc))
            return
        if path.exists() and CdwMessageBox.question(self, TEXT["replace_title"], TEXT["replace_body"].format(path=path)) != CdwMessageBox.Yes:
            return
        self.path, self.delimiter = path, delimiter
        self.accept()


class SaveMemoryDialog(ResultActionDialog):
    def __init__(self, title, parent=None, *, theme=None):
        super().__init__(TEXT["save_title"], parent, theme=theme)
        self.name_edit = CdwLineEdit(suggested_name(title), theme=self.theme)
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
        self.task_combo = CdwComboBox(theme=self.theme)
        for task in tasks:
            self.task_combo.addItem(task.name, task.id)
        self.form.addRow(TEXT["task"], self.task_combo)
        self.add_help(TEXT["task_help"])
        self.finish(TEXT["run"], self.accept)

    @property
    def task_id(self):
        return self.task_combo.currentData()
