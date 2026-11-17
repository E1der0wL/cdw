"""Shared command and complete-environment editors used by presets and tasks."""

import os
from pathlib import Path

from qtpy.QtWidgets import QAbstractItemView, QTableWidgetItem

from cdw.domain.contracts import CommandMode, CommandSpec
from cdw.i18n import tr
from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import LayoutRole, TextRole
from cdw.ui.widgets import (
    CdwButton, CdwComboBox, CdwFormLayout, CdwHBoxLayout, CdwLabel,
    CdwLineEdit, CdwPlainTextEdit, CdwTableWidget, CdwVBoxLayout, CdwWidget,
)


class CommandEditor(CdwWidget):
    DEFAULT_SHELL = "/bin/bash"

    def __init__(self, parent=None, *, theme=None):
        super().__init__(parent, theme=theme)
        layout = CdwVBoxLayout(self, role=LayoutRole.CONTENT, theme=self.theme)
        form = CdwFormLayout(role=LayoutRole.FORM, theme=self.theme)
        layout.addLayout(form)
        self.mode_combo = CdwComboBox(theme=self.theme)
        self.mode_combo.setObjectName("command_mode")
        self.mode_combo.addItem(tr("collection.editor.direct"), CommandMode.DIRECT)
        self.mode_combo.addItem(tr("collection.editor.shell"), CommandMode.SHELL)
        form.addRow(tr("collection.editor.mode"), self.mode_combo)
        self.direct_page = CdwWidget(theme=self.theme)
        direct = CdwFormLayout(self.direct_page, role=LayoutRole.FORM, theme=self.theme)
        self.executable_edit = CdwLineEdit(theme=self.theme)
        self.executable_edit.setObjectName("executable")
        self.executable_edit.setPlaceholderText(tr("collection.editor.path_placeholder"))
        self.arguments_edit = CdwPlainTextEdit(code=True, compact=True, theme=self.theme)
        self.arguments_edit.setObjectName("arguments")
        direct.addRow(tr("collection.editor.executable"), self.executable_edit)
        direct.addRow(tr("collection.editor.arguments"), self.arguments_edit)
        layout.addWidget(self.direct_page)
        self.shell_page = CdwWidget(theme=self.theme)
        shell = CdwFormLayout(self.shell_page, role=LayoutRole.FORM, theme=self.theme)
        self.shell_edit = CdwLineEdit(self.DEFAULT_SHELL, theme=self.theme)
        self.shell_edit.setObjectName("shell_executable")
        self.script_edit = CdwPlainTextEdit(code=True, theme=self.theme)
        self.script_edit.setObjectName("shell_script")
        shell.addRow(tr("collection.editor.shell_path"), self.shell_edit)
        shell.addRow(tr("collection.editor.script"), self.script_edit)
        layout.addWidget(self.shell_page)
        directory = CdwFormLayout(role=LayoutRole.FORM, theme=self.theme)
        self.cwd_edit = CdwLineEdit(str(Path.cwd()), theme=self.theme)
        self.cwd_edit.setObjectName("command_directory")
        self.cwd_edit.setPlaceholderText(tr("collection.editor.cwd_placeholder"))
        directory.addRow(tr("collection.editor.cwd"), self.cwd_edit)
        layout.addLayout(directory)
        self.help_label = CdwLabel(role=TextRole.MUTED, theme=self.theme)
        self.help_label.setWordWrap(True)
        layout.addWidget(self.help_label)
        layout.addStretch()
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self._mode_changed()

    def _mode_changed(self, *_):
        shell = CommandMode(self.mode_combo.currentData()) is CommandMode.SHELL
        self.direct_page.setVisible(not shell)
        self.shell_page.setVisible(shell)
        self.help_label.setText(tr("collection.editor.shell_help") if shell else tr("collection.editor.direct_help"))

    def load_command(self, command: CommandSpec):
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(command.mode))
        if command.mode is CommandMode.SHELL:
            self.shell_edit.setText(command.executable)
            self.script_edit.setPlainText(command.script)
        else:
            self.executable_edit.setText(command.executable)
            self.arguments_edit.setPlainText("\n".join(command.arguments))
        self.cwd_edit.setText(command.working_directory or "")
        self._mode_changed()

    def command(self, execution, encoding, environment) -> CommandSpec:
        mode = CommandMode(self.mode_combo.currentData())
        shell = mode is CommandMode.SHELL
        arguments = () if shell else tuple(self.arguments_edit.toPlainText().splitlines())
        return CommandSpec(
            executable=(self.shell_edit.text() if shell else self.executable_edit.text()).strip(),
            arguments=arguments, working_directory=self.cwd_edit.text().strip() or None,
            environment=environment, execution=execution, encoding=encoding, mode=mode,
            script=self.script_edit.toPlainText() if shell else "",
        )


class EnvironmentEditor(CdwWidget):
    NAME_COLUMN = 0
    VALUE_COLUMN = 1
    COLUMN_COUNT = 2

    def __init__(self, parent=None, *, theme=None):
        super().__init__(parent, theme=theme)
        layout = CdwVBoxLayout(self, role=LayoutRole.CONTENT, theme=self.theme)
        toolbar = CdwHBoxLayout(role=LayoutRole.ROW, theme=self.theme)
        self.search_edit = CdwLineEdit(theme=self.theme)
        self.search_edit.setPlaceholderText(tr("collection.environment.search"))
        self.capture_button = CdwButton(tr("collection.environment.capture"), icon=IconName.REFRESH, theme=self.theme)
        self.add_button = CdwButton(tr("collection.environment.add"), icon=IconName.ADD, theme=self.theme)
        self.remove_button = CdwButton(tr("collection.environment.remove"), icon=IconName.DELETE, theme=self.theme)
        toolbar.addWidget(self.search_edit, 1)
        for button in (self.capture_button, self.add_button, self.remove_button):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.table = CdwTableWidget(0, self.COLUMN_COUNT, theme=self.theme)
        self.table.setObjectName("environment_variables")
        self.table.setHorizontalHeaderLabels((tr("collection.environment.name"), tr("collection.environment.value")))
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)
        hint = CdwLabel(tr("collection.environment.help"), role=TextRole.MUTED, theme=self.theme)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.capture_button.clicked.connect(self.capture_current)
        self.add_button.clicked.connect(self.add_variable)
        self.remove_button.clicked.connect(self.remove_selected)
        self.search_edit.textChanged.connect(self._filter)
        self.capture_current()

    def load_environment(self, values):
        self.table.setRowCount(0)
        for name, value in sorted(values.items()):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, self.NAME_COLUMN, QTableWidgetItem(name))
            self.table.setItem(row, self.VALUE_COLUMN, QTableWidgetItem(value))
        self.table.resizeColumnToContents(self.NAME_COLUMN)
        self._filter()

    def capture_current(self):
        self.load_environment(dict(os.environ))

    def add_variable(self):
        self.search_edit.clear()
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column in (self.NAME_COLUMN, self.VALUE_COLUMN):
            self.table.setItem(row, column, QTableWidgetItem(""))
        self.table.setCurrentCell(row, self.NAME_COLUMN)
        self.table.editItem(self.table.item(row, self.NAME_COLUMN))

    def remove_selected(self):
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        for row in sorted(rows, reverse=True):
            self.table.removeRow(row)

    def environment(self):
        result = {}
        for row in range(self.table.rowCount()):
            name = self.table.item(row, self.NAME_COLUMN).text()
            value = self.table.item(row, self.VALUE_COLUMN).text()
            if not name or name in result:
                raise ValueError(tr("collection.environment.invalid"))
            result[name] = value
        return result

    def _filter(self, *_):
        text = self.search_edit.text().casefold()
        for row in range(self.table.rowCount()):
            visible = any(text in self.table.item(row, column).text().casefold()
                          for column in (self.NAME_COLUMN, self.VALUE_COLUMN))
            self.table.setRowHidden(row, not visible)
