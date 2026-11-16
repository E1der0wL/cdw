"""Task editor; execution and input serialization stay outside the UI."""

from __future__ import annotations

import json
from uuid import uuid4

from cdb.application.tasks import TaskService
from cdb.domain.contracts import CdbError, ExecutionPolicy
from cdb.domain.tasks import INPUT_PLACEHOLDER, TaskDefinition, TaskInputFormat
from cdb.domain.workspace import DEFAULT_EXPORT_DELIMITER
from cdb.ui.design.icons import IconName
from cdb.ui.design.spec import LayoutRole, TextRole
from cdb.ui.widgets import (
    CdbComboBox, CdbDialog, CdbDialogButtonBox, CdbFormLayout, CdbLabel,
    CdbLineEdit, CdbPlainTextEdit, CdbScrollArea, CdbTabWidget, CdbVBoxLayout, CdbWidget,
)

TEXT = {
    "title": "작업 등록", "edit_title": "작업 편집", "name": "작업 이름", "command_tab": "명령어",
    "input_tab": "입력 및 실행 제한", "executable": "실행 파일", "arguments": "인자 · 한 줄에 하나",
    "cwd": "작업 디렉터리", "environment": "환경변수 · JSON 객체", "format": "입력 테이블 형식",
    "delimiter": "CSV 구분자", "timeout": "제한 시간 · 초", "output_limit": "stdout + stderr · 바이트",
    "save": "등록", "update": "변경 저장", "cancel": "취소",
    "help": "인자에 {input}을 넣으면 조회 결과를 담은 임시 파일 경로로 바뀝니다. 경로를 따옴표로 감싸지 마세요.\n예: 실행 파일 python3, 인자 /absolute/path/to/process.py 와 {input}을 각각 한 줄에 입력합니다.",
    "input_help": "JSON은 columns·types·rows를 갖는 객체이며, CSV는 첫 행에 컬럼명을 기록합니다. UTF-8을 사용합니다.\n입력 파일은 작업이 끝나면 삭제됩니다. 작업에서 보관할 파일은 별도로 생성하세요.",
    "env_help": "기존 환경변수에 덮어씁니다. 비밀번호 등 비밀 값은 저장하지 마세요.",
    "environment_error": "환경변수는 문자열 키와 문자열 값으로 된 JSON 객체여야 합니다.",
    "error": "작업을 저장하지 못했습니다. {details}",
}


class TaskDialog(CdbDialog):
    def __init__(self, service: TaskService, task: TaskDefinition | None = None, parent=None, *, theme=None):
        super().__init__(parent, theme=theme or getattr(parent, "theme", None))
        self.service = service
        self.original = task
        self.saved_task: TaskDefinition | None = None
        self.setWindowTitle(TEXT["edit_title"] if task else TEXT["title"])
        layout = CdbVBoxLayout(self, role=LayoutRole.DIALOG, theme=self.theme)
        name = CdbFormLayout(role=LayoutRole.ROW, theme=self.theme)
        self.name_edit = CdbLineEdit(theme=self.theme)
        self.name_edit.setObjectName("task_name")
        name.addRow(TEXT["name"], self.name_edit)
        layout.addLayout(name)
        self.tabs = CdbTabWidget(theme=self.theme)
        layout.addWidget(self.tabs, 1)

        command_page = CdbWidget(theme=self.theme)
        command = CdbFormLayout(command_page, theme=self.theme)
        self.executable_edit = CdbLineEdit(theme=self.theme)
        self.executable_edit.setObjectName("task_executable")
        self.arguments_edit = CdbPlainTextEdit(INPUT_PLACEHOLDER, code=True, compact=True, theme=self.theme)
        self.arguments_edit.setObjectName("task_arguments")
        self.cwd_edit = CdbLineEdit(theme=self.theme)
        self.environment_edit = CdbPlainTextEdit("{}", code=True, compact=True, theme=self.theme)
        for key, field in (("executable", self.executable_edit), ("arguments", self.arguments_edit),
                           ("cwd", self.cwd_edit), ("environment", self.environment_edit)):
            command.addRow(TEXT[key], field)
        for key in ("help", "env_help"):
            hint = CdbLabel(TEXT[key], role=TextRole.MUTED, theme=self.theme)
            hint.setWordWrap(True)
            command.addRow(hint)
        self._add_tab(command_page, TEXT["command_tab"])

        input_page = CdbWidget(theme=self.theme)
        inputs = CdbFormLayout(input_page, theme=self.theme)
        self.format_combo = CdbComboBox(theme=self.theme)
        for value in TaskInputFormat:
            self.format_combo.addItem(value.value.upper(), value)
        self.delimiter_edit = CdbLineEdit(DEFAULT_EXPORT_DELIMITER, theme=self.theme)
        self.timeout_edit = CdbLineEdit(str(service.config.execution_policy.timeout_seconds), theme=self.theme)
        self.output_edit = CdbLineEdit(str(service.config.execution_policy.max_output_bytes), theme=self.theme)
        for key, field in (("format", self.format_combo), ("delimiter", self.delimiter_edit),
                           ("timeout", self.timeout_edit), ("output_limit", self.output_edit)):
            inputs.addRow(TEXT[key], field)
        hint = CdbLabel(TEXT["input_help"], role=TextRole.MUTED, theme=self.theme)
        hint.setWordWrap(True)
        inputs.addRow(hint)
        self._add_tab(input_page, TEXT["input_tab"])
        self.format_combo.currentIndexChanged.connect(self._format_changed)
        self.feedback = CdbLabel(role=TextRole.ERROR, theme=self.theme)
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        buttons = CdbDialogButtonBox(theme=self.theme)
        self.save_button = buttons.add_action(TEXT["update"] if task else TEXT["save"],
                                             CdbDialogButtonBox.AcceptRole, icon=IconName.CHECK, primary=True)
        self.save_button.setObjectName("save_task")
        buttons.add_action(TEXT["cancel"], CdbDialogButtonBox.RejectRole, icon=IconName.CLOSE)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if task is not None:
            self._load(task)
        self._format_changed()

    def _add_tab(self, page, title):
        scroll = CdbScrollArea(theme=self.theme)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, title)

    def _load(self, task):
        self.name_edit.setText(task.name)
        self.executable_edit.setText(task.executable)
        self.arguments_edit.setPlainText("\n".join(task.arguments))
        self.cwd_edit.setText(task.working_directory or "")
        self.environment_edit.setPlainText(json.dumps(dict(task.environment), ensure_ascii=False, indent=2))
        self.format_combo.setCurrentIndex(self.format_combo.findData(task.input_format))
        self.delimiter_edit.setText(task.csv_delimiter)
        self.timeout_edit.setText(str(task.execution.timeout_seconds))
        self.output_edit.setText(str(task.execution.max_output_bytes))

    def _format_changed(self):
        self.delimiter_edit.setEnabled(TaskInputFormat(self.format_combo.currentData()) is TaskInputFormat.CSV)

    def _save(self):
        try:
            environment = json.loads(self.environment_edit.toPlainText())
            if not isinstance(environment, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in environment.items()):
                raise ValueError(TEXT["environment_error"])
            task = TaskDefinition(
                id=self.original.id if self.original else str(uuid4()), name=self.name_edit.text(),
                executable=self.executable_edit.text().strip(), arguments=tuple(self.arguments_edit.toPlainText().splitlines()),
                working_directory=self.cwd_edit.text().strip() or None, environment=environment,
                input_format=TaskInputFormat(self.format_combo.currentData()), csv_delimiter=self.delimiter_edit.text(),
                execution=ExecutionPolicy(float(self.timeout_edit.text()), int(self.output_edit.text())),
            )
            self.saved_task = self.service.save_task(task)
        except (CdbError, ValueError) as exc:
            details = f"{exc}\n{exc.details}" if isinstance(exc, CdbError) else str(exc)
            self.feedback.setText(TEXT["error"].format(details=details.strip()))
            return
        self.accept()
