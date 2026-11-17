"""Actual command, environment and parsing configuration; preview never stores data."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from qtpy.QtCore import Qt, Signal
from qtpy.QtWidgets import QTableWidgetItem

from cdw.config import BYTES_PER_MIB, DEFAULT_CSV_DELIMITER, DEFAULT_ENCODING
from cdw.domain.contracts import CdwError, ExecutionPolicy, OutputFormat, Preset, StorageMode, StorageTarget, WriteMode
from cdw.domain.parsing import ColumnCountMode, DelimiterMode, ParseSpec
from cdw.i18n import tr
from cdw.ui.command_editor import CommandEditor, EnvironmentEditor
from cdw.ui.design.icons import IconName
from cdw.ui.design.manager import ThemeManager
from cdw.ui.design.spec import LayoutRole, TextRole
from cdw.ui.widgets import (
    CdwActionLineEdit, CdwButton, CdwComboBox, CdwDialog, CdwDialogButtonBox,
    CdwFileDialog, CdwFormLayout, CdwHBoxLayout, CdwLabel, CdwLineEdit,
    CdwPlainTextEdit, CdwScrollArea, CdwTableWidget, CdwTabWidget, CdwVBoxLayout, CdwWidget,
)


class PresetDialog(CdwDialog):
    DEFAULT_TABLE = "command_result"
    DEFAULT_FIXED_COUNT = 3
    MAX_AUTOFILL_COLUMNS = 256
    PREVIEW_WORKERS = 1
    PREVIEW_WORKER_NAME = "cdw-preview"
    TAB_LITERAL = "\\t"
    FILE_FILTER = tr("ui.preset_dialog.file_filter")
    _preview_completed = Signal(object, object)

    def __init__(self, service, preset: Preset | None = None, parent=None):
        super().__init__(parent, theme=getattr(parent, "theme", None) or ThemeManager.for_service(service))
        self.service = service
        self.original = preset
        self.saved_preset = None
        self._preset_id = preset.id if preset else str(uuid4())
        self._preview_busy = False
        self._closing_after_preview = False
        self._preview_cancel = threading.Event()
        self._preview_executor = None
        self.setWindowTitle(tr("ui.preset_dialog.labels.edit_title") if preset else tr("ui.pages.sql.text.new"))
        layout = CdwVBoxLayout(self, role=LayoutRole.DIALOG, theme=self.theme)
        names = CdwFormLayout(role=LayoutRole.FORM, theme=self.theme)
        self.name_edit = CdwLineEdit(theme=self.theme)
        self.name_edit.setObjectName("preset_name")
        names.addRow(tr("ui.preset_dialog.labels.name"), self.name_edit)
        layout.addLayout(names)
        self.tabs = CdwTabWidget(theme=self.theme)
        layout.addWidget(self.tabs, 1)
        self.command_editor = CommandEditor(theme=self.theme)
        self._add_editor_tab(self.command_editor, tr("collection.preset.command_tab"))
        self.environment_editor = EnvironmentEditor(theme=self.theme)
        self._add_editor_tab(self.environment_editor, tr("collection.preset.environment_tab"))
        self._build_parsing()
        self._build_storage()
        self._build_preview()
        self.feedback = CdwLabel(role=TextRole.ERROR, theme=self.theme)
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        footer = CdwHBoxLayout(role=LayoutRole.ROW, theme=self.theme)
        self.preview_button = CdwButton(tr("collection.preset.preview"), icon=IconName.PLAY, theme=self.theme)
        self.preview_button.setObjectName("preview_command")
        self.preview_button.clicked.connect(self._preview)
        self.stop_preview_button = CdwButton(tr("collection.preset.preview_stop"), icon=IconName.STOP, theme=self.theme)
        self.stop_preview_button.clicked.connect(self._cancel_preview)
        self.stop_preview_button.setEnabled(False)
        footer.addWidget(self.preview_button)
        footer.addWidget(self.stop_preview_button)
        footer.addStretch()
        buttons = CdwDialogButtonBox(theme=self.theme)
        self.save_button = buttons.add_action(tr("ui.preset_dialog.labels.update") if preset else tr("ui.preset_dialog.labels.save"),
                                             CdwDialogButtonBox.AcceptRole, icon=IconName.CHECK, primary=True)
        self.save_button.setObjectName("save_preset")
        buttons.add_action(tr("common.cancel"), CdwDialogButtonBox.RejectRole, icon=IconName.CLOSE)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)
        self._preview_completed.connect(self._finish_preview, Qt.QueuedConnection)
        if preset:
            self._load(preset)
        self._parsing_changed()
        self._storage_changed()

    def _add_tab(self, page, title):
        scroll = CdwScrollArea(theme=self.theme)
        scroll.setWidget(page)
        self.tabs.addTab(scroll, title)
        return scroll

    def _add_editor_tab(self, editor, title):
        page = CdwWidget(theme=self.theme)
        layout = CdwVBoxLayout(page, role=LayoutRole.SECTION, theme=self.theme)
        layout.addWidget(editor)
        return self._add_tab(page, title)

    def _form_page(self):
        page = CdwWidget(theme=self.theme)
        layout = CdwVBoxLayout(page, role=LayoutRole.SECTION, theme=self.theme)
        form = CdwFormLayout(role=LayoutRole.FORM, theme=self.theme)
        layout.addLayout(form)
        return page, layout, form

    def _help(self, text, layout):
        label = CdwLabel(text, role=TextRole.MUTED, theme=self.theme)
        label.setWordWrap(True)
        layout.addWidget(label)
        return label

    def _build_parsing(self):
        page, layout, form = self._form_page()
        self.parsing_form = form
        self.format_combo = CdwComboBox(theme=self.theme)
        self.format_combo.addItem(tr("collection.preset.delimited"), OutputFormat.CSV)
        for format in (OutputFormat.JSON, OutputFormat.NDJSON, OutputFormat.TEXT):
            self.format_combo.addItem(format.value.upper(), format)
        self.encoding_edit = CdwLineEdit(DEFAULT_ENCODING, theme=self.theme)
        self.header_combo = CdwComboBox(theme=self.theme)
        self.header_combo.addItem(tr("collection.preset.header_yes"), True)
        self.header_combo.addItem(tr("collection.preset.header_no"), False)
        self.column_mode_combo = CdwComboBox(theme=self.theme)
        self.column_mode_combo.addItem(tr("collection.preset.dynamic"), ColumnCountMode.DYNAMIC)
        self.column_mode_combo.addItem(tr("collection.preset.fixed"), ColumnCountMode.FIXED)
        self.column_count_edit = CdwLineEdit(str(self.DEFAULT_FIXED_COUNT), theme=self.theme)
        self.column_count_edit.setObjectName("fixed_column_count")
        self.column_names_edit = CdwPlainTextEdit(compact=True, theme=self.theme)
        self.column_names_edit.setObjectName("fixed_column_names")
        self.delimiter_mode_combo = CdwComboBox(theme=self.theme)
        self.delimiter_mode_combo.addItem(tr("collection.preset.character"), DelimiterMode.CHARACTER)
        self.delimiter_mode_combo.addItem(tr("collection.preset.whitespace"), DelimiterMode.WHITESPACE)
        self.delimiter_edit = CdwLineEdit(DEFAULT_CSV_DELIMITER, theme=self.theme)
        self.infer_types_combo = CdwComboBox(theme=self.theme)
        self.infer_types_combo.addItem(tr("collection.preset.infer_types"), True)
        self.infer_types_combo.addItem(tr("collection.preset.all_strings"), False)
        fields = (
            (tr("ui.preset_dialog.labels.format"), self.format_combo),
            (tr("ui.preset_dialog.labels.encoding"), self.encoding_edit),
            (tr("collection.preset.header"), self.header_combo),
            (tr("collection.preset.column_mode"), self.column_mode_combo),
            (tr("collection.preset.column_count"), self.column_count_edit),
            (tr("collection.preset.column_names"), self.column_names_edit),
            (tr("collection.preset.delimiter_mode"), self.delimiter_mode_combo),
            (tr("collection.preset.delimiter_character"), self.delimiter_edit),
            (tr("collection.preset.type_inference"), self.infer_types_combo),
        )
        for label, widget in fields:
            form.addRow(label, widget)
        for message in (tr("collection.preset.delimiter_help"), tr("collection.preset.fixed_help"),
                        tr("collection.preset.parsing_help"), tr("collection.preset.format_help")):
            self._help(message, layout)
        layout.addStretch()
        self._add_tab(page, tr("collection.preset.parsing_tab"))
        self.format_combo.currentIndexChanged.connect(self._parsing_changed)
        self.column_mode_combo.currentIndexChanged.connect(self._parsing_changed)
        self.delimiter_mode_combo.currentIndexChanged.connect(self._parsing_changed)
        self.column_count_edit.editingFinished.connect(self._sync_column_names)
        self._sync_column_names()

    def _build_storage(self):
        page, layout, form = self._form_page()
        self.storage_combo = CdwComboBox(theme=self.theme)
        self.storage_combo.addItem(tr("ui.preset_dialog.storage_labels_2"), StorageMode.MEMORY)
        self.storage_combo.addItem(tr("ui.preset_dialog.storage_labels"), StorageMode.FILE)
        self.path_edit = CdwActionLineEdit(str(self.service.config.default_database), action_text=tr("ui.preset_dialog.labels.browse"), action_icon=IconName.FOLDER, theme=self.theme)
        self.path_edit.action_button.clicked.connect(self._browse)
        self.table_edit = CdwLineEdit(self.DEFAULT_TABLE, theme=self.theme)
        self.table_edit.setObjectName("table_name")
        self.write_combo = CdwComboBox(theme=self.theme)
        self.write_combo.addItem(tr("ui.preset_dialog.write_labels"), WriteMode.REPLACE)
        self.write_combo.addItem(tr("ui.preset_dialog.write_labels_2"), WriteMode.APPEND)
        self.timeout_edit = CdwLineEdit(str(self.service.config.execution_policy.timeout_seconds), theme=self.theme)
        self.output_edit = CdwLineEdit(str(self.service.config.execution_policy.max_output_bytes), theme=self.theme)
        for label, widget in ((tr("ui.preset_dialog.labels.storage"), self.storage_combo),
                              (tr("ui.preset_dialog.labels.path"), self.path_edit),
                              (tr("ui.preset_dialog.labels.table"), self.table_edit),
                              (tr("ui.preset_dialog.labels.write"), self.write_combo),
                              (tr("ui.preset_dialog.labels.timeout"), self.timeout_edit),
                              (tr("ui.preset_dialog.labels.output"), self.output_edit)):
            form.addRow(label, widget)
        self._help(tr("ui.preset_dialog.labels.storage_help"), layout)
        self._help(tr("ui.preset_dialog.labels.limits_help").format(bytes=BYTES_PER_MIB), layout)
        layout.addStretch()
        self._add_tab(page, tr("collection.preset.storage_tab"))
        self.storage_combo.currentIndexChanged.connect(self._storage_changed)

    def _build_preview(self):
        page = CdwWidget(theme=self.theme)
        layout = CdwVBoxLayout(page, role=LayoutRole.SECTION, theme=self.theme)
        self._help(tr("collection.preset.preview_help"), layout)
        self.preview_status = CdwLabel(tr("collection.preset.preview_empty"), role=TextRole.MUTED, theme=self.theme)
        self.preview_status.setWordWrap(True)
        layout.addWidget(self.preview_status)
        self.preview_tabs = CdwTabWidget(theme=self.theme)
        self.raw_output = CdwPlainTextEdit(code=True, theme=self.theme)
        self.raw_output.setReadOnly(True)
        self.stderr_output = CdwPlainTextEdit(code=True, theme=self.theme)
        self.stderr_output.setReadOnly(True)
        self.preview_table = CdwTableWidget(0, 0, theme=self.theme)
        self.preview_table.setObjectName("command_preview_table")
        self.preview_tabs.addTab(self.preview_table, tr("collection.preset.parsed_output"))
        self.preview_tabs.addTab(self.raw_output, tr("collection.preset.raw_output"))
        self.preview_tabs.addTab(self.stderr_output, tr("collection.preset.stderr"))
        layout.addWidget(self.preview_tabs, 1)
        self.preview_page = self._add_tab(page, tr("collection.preset.preview_tab"))

    def _load(self, preset):
        self.name_edit.setText(preset.name)
        self.command_editor.load_command(preset.command)
        self.environment_editor.load_environment(preset.environment)
        self.format_combo.setCurrentIndex(self.format_combo.findData(preset.output_format))
        self.encoding_edit.setText(preset.encoding)
        self.delimiter_edit.setText(self.TAB_LITERAL if preset.csv_delimiter == "\t" else preset.csv_delimiter)
        self.header_combo.setCurrentIndex(self.header_combo.findData(preset.parsing.has_header))
        self.column_mode_combo.setCurrentIndex(self.column_mode_combo.findData(preset.parsing.column_mode))
        if preset.parsing.column_count is not None:
            self.column_count_edit.setText(str(preset.parsing.column_count))
        self.column_names_edit.setPlainText("\n".join(preset.parsing.column_names))
        self._sync_column_names()
        self.delimiter_mode_combo.setCurrentIndex(self.delimiter_mode_combo.findData(preset.parsing.delimiter_mode))
        self.infer_types_combo.setCurrentIndex(self.infer_types_combo.findData(preset.parsing.infer_types))
        self.storage_combo.setCurrentIndex(self.storage_combo.findData(preset.storage.mode))
        self.path_edit.setText(preset.storage.path or str(self.service.config.default_database))
        self.table_edit.setText(preset.table_name)
        self.write_combo.setCurrentIndex(self.write_combo.findData(preset.write_mode))
        self.timeout_edit.setText(str(preset.execution.timeout_seconds))
        self.output_edit.setText(str(preset.execution.max_output_bytes))

    def _parsing_changed(self, *_):
        delimited = OutputFormat(self.format_combo.currentData()) is OutputFormat.CSV
        fixed = ColumnCountMode(self.column_mode_combo.currentData()) is ColumnCountMode.FIXED
        character = DelimiterMode(self.delimiter_mode_combo.currentData()) is DelimiterMode.CHARACTER
        for widget in (self.header_combo, self.column_mode_combo, self.delimiter_mode_combo, self.infer_types_combo):
            widget.setEnabled(delimited)
        for widget in (self.column_count_edit, self.column_names_edit):
            widget.setEnabled(delimited and fixed)
        self.delimiter_edit.setEnabled(delimited and character)
        self.parsing_form.setRowVisible(self.delimiter_edit, delimited and character)

    def _sync_column_names(self):
        try:
            count = int(self.column_count_edit.text())
        except ValueError:
            return
        if not 1 <= count <= self.MAX_AUTOFILL_COLUMNS:
            return
        names = self.column_names_edit.toPlainText().splitlines()
        # The domain validates the requested count; this only fills visible names.
        names = names[:count] + list(ParseSpec.generated_names(count)[len(names):])
        self.column_names_edit.setPlainText("\n".join(names))

    def _storage_changed(self, *_):
        self.path_edit.setEnabled(StorageMode(self.storage_combo.currentData()) is StorageMode.FILE)

    def _browse(self):
        path, _ = CdwFileDialog.getSaveFileName(self, tr("ui.preset_dialog.labels.path"), self.path_edit.text(), self.FILE_FILTER)
        if path:
            self.path_edit.setText(path)

    def _candidate(self, *, preview=False):
        execution = ExecutionPolicy(float(self.timeout_edit.text()), int(self.output_edit.text()))
        command = self.command_editor.command(execution, self.encoding_edit.text().strip(), self.environment_editor.environment())
        format = OutputFormat(self.format_combo.currentData())
        mode = ColumnCountMode(self.column_mode_combo.currentData())
        parsing = (ParseSpec(has_header=bool(self.header_combo.currentData()), column_mode=mode,
                             column_count=int(self.column_count_edit.text()) if mode is ColumnCountMode.FIXED else None,
                             column_names=tuple(self.column_names_edit.toPlainText().splitlines()) if mode is ColumnCountMode.FIXED else (),
                             delimiter_mode=DelimiterMode(self.delimiter_mode_combo.currentData()),
                             infer_types=bool(self.infer_types_combo.currentData()))
                   if format is OutputFormat.CSV else ParseSpec())
        storage = StorageMode(self.storage_combo.currentData())
        delimiter = "\t" if self.delimiter_edit.text() == self.TAB_LITERAL else self.delimiter_edit.text()
        if format is not OutputFormat.CSV or parsing.delimiter_mode is DelimiterMode.WHITESPACE:
            delimiter = DEFAULT_CSV_DELIMITER
        return Preset(
            id=self._preset_id, name=self.name_edit.text().strip() or (tr("collection.preset.preview") if preview else ""),
            executable=command.executable, arguments=command.arguments, working_directory=command.working_directory,
            environment=command.environment, output_format=format, encoding=command.encoding, csv_delimiter=delimiter,
            storage=StorageTarget(storage, self.path_edit.text().strip() if storage is StorageMode.FILE else None),
            table_name=self.table_edit.text().strip(), write_mode=WriteMode(self.write_combo.currentData()),
            execution=execution, command_mode=command.mode, shell_script=command.script, parsing=parsing,
        )

    def _save(self):
        try:
            self.saved_preset = self.service.save_preset(self._candidate())
        except (CdwError, ValueError) as error:
            self._show_error(error)
            return
        self.accept()

    def _show_error(self, error):
        details = error.details if isinstance(error, CdwError) else ""
        self.feedback.setText(f"{error}\n{details}".strip())

    def _preview(self):
        if self._preview_busy:
            return
        try:
            candidate = self._candidate(preview=True)
        except (CdwError, ValueError) as error:
            self._show_error(error)
            return
        self.feedback.clear()
        self._preview_cancel = threading.Event()
        self._preview_busy = True
        self.preview_button.setEnabled(False)
        self.stop_preview_button.setEnabled(True)
        self.save_button.setEnabled(False)
        self.preview_status.setText(tr("collection.preset.preview_running"))
        self.tabs.setCurrentWidget(self.preview_page)
        self._preview_executor = ThreadPoolExecutor(max_workers=self.PREVIEW_WORKERS, thread_name_prefix=self.PREVIEW_WORKER_NAME)
        future = self._preview_executor.submit(self.service.preview, candidate, self._preview_cancel)

        def completed(future):
            try:
                value = future.result()
            except Exception as error:
                self._preview_completed.emit(None, error)
            else:
                self._preview_completed.emit(value, None)
        future.add_done_callback(completed)

    def _cancel_preview(self):
        self._preview_cancel.set()
        self.stop_preview_button.setEnabled(False)

    def _finish_preview(self, result, error):
        self._preview_busy = False
        if self._preview_executor is not None:
            self._preview_executor.shutdown(wait=False)
            self._preview_executor = None
        self.preview_button.setEnabled(True)
        self.stop_preview_button.setEnabled(False)
        self.save_button.setEnabled(True)
        if self._closing_after_preview:
            super().reject()
            return
        if error is not None:
            self._show_error(error)
            self.preview_status.setText(tr("collection.preset.preview_failure"))
            return
        self.raw_output.setPlainText(result.stdout)
        self.stderr_output.setPlainText(result.stderr)
        self.preview_table.setColumnCount(len(result.columns))
        self.preview_table.setRowCount(len(result.rows))
        self.preview_table.setHorizontalHeaderLabels(result.columns)
        for row, values in enumerate(result.rows):
            for column, value in enumerate(values):
                self.preview_table.setItem(row, column, QTableWidgetItem("" if value is None else str(value)))
        self.preview_table.resizeColumnsToContents()
        self.preview_status.setText(result.parse_error or tr("collection.preset.preview_done").format(
            rows=result.total_rows, columns=len(result.columns), seconds=result.elapsed_seconds))
        if result.total_rows > len(result.rows):
            self.feedback.setText(tr("collection.preset.preview_capped").format(rows=result.total_rows, shown=len(result.rows)))
        if result.parse_error:
            self.preview_tabs.setCurrentWidget(self.raw_output)

    def reject(self):
        if self._preview_busy:
            self._closing_after_preview = True
            self._cancel_preview()
            self.preview_status.setText(tr("collection.preset.preview_close_wait"))
            return
        super().reject()

    def closeEvent(self, event):
        if self._preview_busy:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
