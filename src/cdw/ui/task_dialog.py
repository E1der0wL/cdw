"""External task editor using the same command/environment controls as collection."""

from __future__ import annotations

from uuid import uuid4

from cdw.application.tasks import TaskService
from cdw.domain.contracts import CdwError, ExecutionPolicy
from cdw.domain.tasks import TASK_ENCODING, TaskDefinition, TaskInputFormat
from cdw.domain.workspace import DEFAULT_EXPORT_DELIMITER
from cdw.i18n import tr
from cdw.ui.command_editor import CommandEditor, EnvironmentEditor
from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import LayoutRole, TextRole
from cdw.ui.widgets import (
    CdwComboBox, CdwDialog, CdwDialogButtonBox, CdwFormLayout, CdwLabel,
    CdwLineEdit, CdwScrollArea, CdwTabWidget, CdwVBoxLayout, CdwWidget,
)


class TaskDialog(CdwDialog):
    INPUT_LABELS = {
        TaskInputFormat.NONE: tr("tasks.input.none"),
        TaskInputFormat.JSON: tr("tasks.input.json"),
        TaskInputFormat.CSV: tr("tasks.input.csv"),
    }
    INPUT_HELP = {
        TaskInputFormat.NONE: tr("tasks.dialog.input_none_help"),
        TaskInputFormat.JSON: tr("tasks.dialog.input_json_help"),
        TaskInputFormat.CSV: tr("tasks.dialog.input_csv_help"),
    }
    TEXT = {
        "title": tr("ui.pages.tasks.text.new"), "edit_title": tr("ui.task_dialog.text.edit_title"),
        "name": tr("ui.task_dialog.text.name"), "command_tab": tr("ui.preset_dialog.labels.command_tab"),
        "environment_tab": tr("tasks.dialog.environment_tab"), "input_tab": tr("tasks.dialog.input_tab"),
        "input_mode": tr("tasks.dialog.input_mode"), "delimiter": tr("ui.preset_dialog.labels.delimiter"),
        "timeout": tr("ui.preset_dialog.labels.timeout"), "output_limit": tr("ui.preset_dialog.labels.output"),
        "placeholder_help": tr("tasks.dialog.input_placeholder_help"),
        "environment_help": tr("tasks.dialog.environment_help"),
        "save": tr("ui.preset_dialog.labels.save"), "update": tr("ui.preset_dialog.labels.update"),
        "cancel": tr("common.cancel"), "error": tr("ui.task_dialog.text.error"),
    }

    def __init__(self, service: TaskService, task: TaskDefinition | None = None, parent=None, *, theme=None):
        super().__init__(parent, theme=theme or getattr(parent, "theme", None))
        self.service = service
        self.original = task
        self.saved_task: TaskDefinition | None = None
        self.setWindowTitle(self.TEXT["edit_title"] if task else self.TEXT["title"])
        layout = CdwVBoxLayout(self, role=LayoutRole.DIALOG, theme=self.theme)
        name = CdwFormLayout(role=LayoutRole.FORM, theme=self.theme)
        self.name_edit = CdwLineEdit(theme=self.theme)
        self.name_edit.setObjectName("task_name")
        name.addRow(self.TEXT["name"], self.name_edit)
        layout.addLayout(name)
        self.tabs = CdwTabWidget(theme=self.theme)
        layout.addWidget(self.tabs, 1)

        self.command_editor = CommandEditor(theme=self.theme)
        command_page = CdwWidget(theme=self.theme)
        command_layout = CdwVBoxLayout(command_page, role=LayoutRole.SECTION, theme=self.theme)
        command_layout.addWidget(self.command_editor)
        self._add_tab(command_page, self.TEXT["command_tab"])
        environment_page = CdwWidget(theme=self.theme)
        environment_layout = CdwVBoxLayout(environment_page, role=LayoutRole.SECTION, theme=self.theme)
        self.environment_editor = EnvironmentEditor(theme=self.theme)
        environment_layout.addWidget(self.environment_editor, 1)
        environment_help = CdwLabel(self.TEXT["environment_help"], role=TextRole.MUTED, theme=self.theme)
        environment_help.setWordWrap(True)
        environment_layout.addWidget(environment_help)
        self._add_tab(environment_page, self.TEXT["environment_tab"])

        input_page = CdwWidget(theme=self.theme)
        input_layout = CdwVBoxLayout(input_page, role=LayoutRole.SECTION, theme=self.theme)
        inputs = CdwFormLayout(theme=self.theme)
        input_layout.addLayout(inputs)
        self.format_combo = CdwComboBox(theme=self.theme)
        self.format_combo.setObjectName("task_input_mode")
        for value, label in self.INPUT_LABELS.items():
            self.format_combo.addItem(label, value)
        self.delimiter_edit = CdwLineEdit(DEFAULT_EXPORT_DELIMITER, theme=self.theme)
        self.delimiter_edit.setObjectName("task_csv_delimiter")
        self.timeout_edit = CdwLineEdit(str(service.config.execution_policy.timeout_seconds), theme=self.theme)
        self.timeout_edit.setObjectName("task_timeout")
        self.output_edit = CdwLineEdit(str(service.config.execution_policy.max_output_bytes), theme=self.theme)
        self.output_edit.setObjectName("task_output_limit")
        for key, field in (("input_mode", self.format_combo), ("delimiter", self.delimiter_edit),
                           ("timeout", self.timeout_edit), ("output_limit", self.output_edit)):
            inputs.addRow(self.TEXT[key], field)
        self.input_hint = CdwLabel(role=TextRole.MUTED, theme=self.theme)
        self.input_hint.setWordWrap(True)
        inputs.addRow(self.input_hint)
        self.placeholder_hint = CdwLabel(self.TEXT["placeholder_help"], role=TextRole.MUTED, theme=self.theme)
        self.placeholder_hint.setWordWrap(True)
        inputs.addRow(self.placeholder_hint)
        input_layout.addStretch()
        self._add_tab(input_page, self.TEXT["input_tab"])
        self.format_combo.currentIndexChanged.connect(self._format_changed)

        self.feedback = CdwLabel(role=TextRole.ERROR, theme=self.theme)
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        buttons = CdwDialogButtonBox(theme=self.theme)
        self.save_button = buttons.add_action(self.TEXT["update"] if task else self.TEXT["save"],
                                             CdwDialogButtonBox.AcceptRole, icon=IconName.CHECK, primary=True)
        self.save_button.setObjectName("save_task")
        buttons.add_action(self.TEXT["cancel"], CdwDialogButtonBox.RejectRole, icon=IconName.CLOSE)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if task is not None:
            self._load(task)
        self._format_changed()

    def _add_tab(self, page, title):
        scroll = CdwScrollArea(theme=self.theme)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, title)

    def _load(self, task):
        self.name_edit.setText(task.name)
        self.command_editor.load_command(task.command)
        self.environment_editor.load_environment(task.environment)
        self.format_combo.setCurrentIndex(self.format_combo.findData(task.input_format))
        self.delimiter_edit.setText(task.csv_delimiter)
        self.timeout_edit.setText(str(task.execution.timeout_seconds))
        self.output_edit.setText(str(task.execution.max_output_bytes))

    def _format_changed(self):
        selected = TaskInputFormat(self.format_combo.currentData())
        self.delimiter_edit.setEnabled(selected is TaskInputFormat.CSV)
        self.input_hint.setText(self.INPUT_HELP[selected])
        self.placeholder_hint.setVisible(selected is not TaskInputFormat.NONE)

    def _save(self):
        try:
            policy = ExecutionPolicy(float(self.timeout_edit.text()), int(self.output_edit.text()))
            command = self.command_editor.command(policy, TASK_ENCODING, self.environment_editor.environment())
            task = TaskDefinition(
                id=self.original.id if self.original else str(uuid4()), name=self.name_edit.text(),
                executable=command.executable, arguments=command.arguments,
                working_directory=command.working_directory, environment=command.environment,
                input_format=TaskInputFormat(self.format_combo.currentData()), csv_delimiter=self.delimiter_edit.text(),
                execution=command.execution, command_mode=command.mode, script=command.script,
            )
            self.saved_task = self.service.save_task(task)
        except (CdwError, ValueError) as exc:
            details = f"{exc}\n{exc.details}" if isinstance(exc, CdwError) else str(exc)
            self.feedback.setText(self.TEXT["error"].format(details=details.strip()))
            return
        self.accept()
