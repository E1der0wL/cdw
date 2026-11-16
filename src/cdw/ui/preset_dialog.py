from __future__ import annotations

import json
from uuid import uuid4

from cdw.ui.design.icons import IconName
from cdw.ui.design.manager import ThemeManager
from cdw.ui.design.spec import LayoutRole, TextRole
from cdw.ui.widgets import (
    CdwActionLineEdit, CdwComboBox, CdwDialog, CdwDialogButtonBox, CdwFileDialog,
    CdwFormLayout, CdwHBoxLayout, CdwLabel, CdwLineEdit, CdwMessageBox,
    CdwPlainTextEdit, CdwButton, CdwScrollArea, CdwTabWidget, CdwVBoxLayout, CdwWidget,
)

from cdw.application.service import ApplicationService
from cdw.application.examples import demo_preset
from cdw.config import BYTES_PER_MIB, DEFAULT_CSV_DELIMITER, DEFAULT_ENCODING
from cdw.domain.contracts import (
    CdwError, ExecutionPolicy, OutputFormat, Preset, StorageMode, StorageTarget, WriteMode,
)

DEFAULT_TABLE = "command_result"
STORAGE_LABELS = {StorageMode.FILE: "파일 · 종료 후에도 유지", StorageMode.MEMORY: "메모리 · 앱 종료 시 데이터 삭제"}
WRITE_LABELS = {WriteMode.REPLACE: "전체 교체", WriteMode.APPEND: "행 추가 · 컬럼과 자료형이 같아야 함"}
LABELS = {
    "title": "프리셋 등록", "edit_title": "프리셋 편집", "demo": "데모 채우기",
    "name": "프리셋 이름", "command": "실행 파일", "arguments": "인자 · 한 줄에 하나",
    "cwd": "작업 디렉터리", "env": "환경변수 · JSON 객체", "format": "출력 형식",
    "encoding": "문자 인코딩", "delimiter": "CSV 구분자", "storage": "저장 위치",
    "path": "DuckDB 파일", "table": "테이블 이름", "write": "저장 방식",
    "timeout": "제한 시간 · 초", "output": "stdout + stderr · 바이트", "save": "등록",
    "limits_help": "1 MiB = {bytes:,}바이트입니다. 제한 시간은 소수 입력이 가능합니다.",
    "update": "변경 저장", "cancel": "취소", "browse": "찾기…",
    "command_tab": "명령어", "storage_tab": "파싱 및 저장", "limits_tab": "실행 제한",
    "help": "실행 파일과 인자를 분리합니다. 따옴표로 감싸지 마세요.\n파이프가 필요하면 /bin/bash에 -c와 스크립트를 각각 인자로 전달하세요.",
    "storage_help": "결과는 source.<테이블 이름>에 저장됩니다.\n빈 출력·파싱 오류·실행 실패 시 기존 테이블은 유지됩니다.",
    "environment_help": "기존 환경변수에 덮어씁니다. 비밀번호 등 비밀 값은 저장하지 마세요.",
    "error": "프리셋을 저장하지 못했습니다.", "env_error": "환경변수는 문자열 키와 문자열 값으로 된 JSON 객체여야 합니다.",
}
FILE_FILTER = "DuckDB (*.duckdb);;모든 파일 (*)"


class PresetDialog(CdwDialog):
    def __init__(self, service: ApplicationService, preset: Preset | None = None, parent=None) -> None:
        super().__init__(parent, theme=getattr(parent, "theme", None) or ThemeManager.for_service(service))
        self.service = service
        self.original = preset
        self.saved_preset: Preset | None = None
        self.setWindowTitle(LABELS["edit_title"] if preset else LABELS["title"])
        layout = CdwVBoxLayout(self, role=LayoutRole.DIALOG)
        self.name_edit = CdwLineEdit()
        self.name_edit.setObjectName("preset_name")
        name_form = CdwFormLayout(role=LayoutRole.ROW)
        name_form.addRow(LABELS["name"], self.name_edit)
        layout.addLayout(name_form)
        self.tabs = CdwTabWidget()
        layout.addWidget(self.tabs)

        command_page = CdwWidget()
        command = CdwFormLayout(command_page)
        self.executable_edit = CdwLineEdit()
        self.executable_edit.setObjectName("executable")
        self.arguments_edit = CdwPlainTextEdit(code=True, compact=True)
        self.arguments_edit.setObjectName("arguments")
        self.cwd_edit = CdwLineEdit()
        self.environment_edit = CdwPlainTextEdit("{}", code=True, compact=True)
        command.addRow(LABELS["command"], self.executable_edit)
        command.addRow(LABELS["arguments"], self.arguments_edit)
        command.addRow(LABELS["cwd"], self.cwd_edit)
        command.addRow(LABELS["env"], self.environment_edit)
        for message in (LABELS["help"], LABELS["environment_help"]):
            label = CdwLabel(message, role=TextRole.MUTED)
            label.setWordWrap(True)
            command.addRow(label)
        self._add_tab(command_page, LABELS["command_tab"])

        storage_page = CdwWidget()
        storage = CdwFormLayout(storage_page)
        self.format_combo = CdwComboBox()
        for value in OutputFormat:
            self.format_combo.addItem(value.value.upper(), value)
        self.encoding_edit = CdwLineEdit(DEFAULT_ENCODING)
        self.delimiter_edit = CdwLineEdit(DEFAULT_CSV_DELIMITER)
        self.storage_combo = CdwComboBox()
        for value, label in STORAGE_LABELS.items():
            self.storage_combo.addItem(label, value)
        self.path_edit = CdwActionLineEdit(str(service.config.default_database), action_text=LABELS["browse"], action_icon=IconName.FOLDER)
        self.path_button = self.path_edit.action_button
        self.path_button.clicked.connect(self._browse)
        self.table_edit = CdwLineEdit(DEFAULT_TABLE)
        self.table_edit.setObjectName("table_name")
        self.write_combo = CdwComboBox()
        for value, label in WRITE_LABELS.items():
            self.write_combo.addItem(label, value)
        storage.addRow(LABELS["format"], self.format_combo)
        storage.addRow(LABELS["encoding"], self.encoding_edit)
        storage.addRow(LABELS["delimiter"], self.delimiter_edit)
        storage.addRow(LABELS["storage"], self.storage_combo)
        storage.addRow(LABELS["path"], self.path_edit)
        storage.addRow(LABELS["table"], self.table_edit)
        storage.addRow(LABELS["write"], self.write_combo)
        help_label = CdwLabel(LABELS["storage_help"], role=TextRole.MUTED)
        help_label.setWordWrap(True)
        storage.addRow(help_label)
        self.storage_combo.currentIndexChanged.connect(self._storage_changed)
        self.format_combo.currentIndexChanged.connect(self._format_changed)
        self._add_tab(storage_page, LABELS["storage_tab"])

        limits_page = CdwWidget()
        limits = CdwFormLayout(limits_page)
        self.timeout_edit = CdwLineEdit(str(service.config.execution_policy.timeout_seconds))
        self.output_edit = CdwLineEdit(str(service.config.execution_policy.max_output_bytes))
        limits.addRow(LABELS["timeout"], self.timeout_edit)
        limits.addRow(LABELS["output"], self.output_edit)
        limits_hint = CdwLabel(LABELS["limits_help"].format(bytes=BYTES_PER_MIB), role=TextRole.MUTED)
        limits_hint.setWordWrap(True)
        limits.addRow(limits_hint)
        self._add_tab(limits_page, LABELS["limits_tab"])

        footer = CdwHBoxLayout()
        self.demo_button = CdwButton(LABELS["demo"], icon=IconName.DOCUMENT)
        self.demo_button.setObjectName("fill_demo")
        self.demo_button.clicked.connect(self.fill_demo)
        footer.addWidget(self.demo_button)
        footer.addStretch()
        buttons = CdwDialogButtonBox()
        self.save_button = buttons.add_action(LABELS["update"] if preset else LABELS["save"],
                                             CdwDialogButtonBox.AcceptRole, icon=IconName.CHECK, primary=True)
        self.save_button.setObjectName("save_preset")
        buttons.add_action(LABELS["cancel"], CdwDialogButtonBox.RejectRole, icon=IconName.CLOSE)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)
        if preset:
            self._load(preset)
        self._storage_changed()
        self._format_changed()

    def _add_tab(self, page, title):
        scroll = CdwScrollArea()
        scroll.setWidget(page)
        self.tabs.addTab(scroll, title)

    def _load(self, preset: Preset) -> None:
        self.name_edit.setText(preset.name)
        self.executable_edit.setText(preset.executable)
        self.arguments_edit.setPlainText("\n".join(preset.arguments))
        self.cwd_edit.setText(preset.working_directory or "")
        self.environment_edit.setPlainText(json.dumps(dict(preset.environment), ensure_ascii=False, indent=2))
        self.format_combo.setCurrentIndex(self.format_combo.findData(preset.output_format))
        self.encoding_edit.setText(preset.encoding)
        self.delimiter_edit.setText(preset.csv_delimiter)
        self.storage_combo.setCurrentIndex(self.storage_combo.findData(preset.storage.mode))
        self.path_edit.setText(preset.storage.path or str(self.service.config.default_database))
        self.table_edit.setText(preset.table_name)
        self.write_combo.setCurrentIndex(self.write_combo.findData(preset.write_mode))
        self.timeout_edit.setText(str(preset.execution.timeout_seconds))
        self.output_edit.setText(str(preset.execution.max_output_bytes))

    def fill_demo(self) -> None:
        self._load(demo_preset(self.service.config))

    def _browse(self) -> None:
        path, _ = CdwFileDialog.getSaveFileName(self, LABELS["path"], self.path_edit.text(), FILE_FILTER)
        if path:
            self.path_edit.setText(path)

    def _storage_changed(self) -> None:
        enabled = StorageMode(self.storage_combo.currentData()) is StorageMode.FILE
        self.path_edit.setEnabled(enabled)
        self.path_button.setEnabled(enabled)

    def _format_changed(self) -> None:
        self.delimiter_edit.setEnabled(OutputFormat(self.format_combo.currentData()) is OutputFormat.CSV)

    def _save(self) -> None:
        try:
            environment = json.loads(self.environment_edit.toPlainText())
            if not isinstance(environment, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in environment.items()):
                raise ValueError(LABELS["env_error"])
            mode = StorageMode(self.storage_combo.currentData())
            arguments = self.arguments_edit.toPlainText()
            preset = Preset(
                id=self.original.id if self.original else str(uuid4()), name=self.name_edit.text(),
                executable=self.executable_edit.text().strip(), arguments=tuple(arguments.splitlines()),
                working_directory=self.cwd_edit.text().strip() or None, environment=environment,
                output_format=OutputFormat(self.format_combo.currentData()), encoding=self.encoding_edit.text().strip(),
                csv_delimiter=self.delimiter_edit.text(),
                storage=StorageTarget(mode, self.path_edit.text().strip() if mode is StorageMode.FILE else None),
                table_name=self.table_edit.text().strip(), write_mode=WriteMode(self.write_combo.currentData()),
                execution=ExecutionPolicy(float(self.timeout_edit.text()), int(self.output_edit.text())),
            )
            self.saved_preset = self.service.save_preset(preset)
        except (CdwError, ValueError) as exc:
            details = exc.details if isinstance(exc, CdwError) else ""
            CdwMessageBox.warning(self, LABELS["error"], f"{exc}\n{details}".strip())
            return
        self.accept()
