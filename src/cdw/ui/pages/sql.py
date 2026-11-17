from __future__ import annotations

from cdw.i18n import tr
from cdw.ui.formatting import format_time, EMPTY_VALUE

from datetime import datetime

from qtpy.QtCore import QMimeData, Qt, Signal
from qtpy.QtGui import QFontMetrics, QKeySequence, QShortcut
from qtpy.QtWidgets import QApplication, QHeaderView, QTreeWidgetItem

from cdw.domain.contracts import CdwError, StorageMode
from cdw.ui.controller import Operation
from cdw.domain.workspace import CopyFormat, ExportFormat
from cdw.ui.result_actions import ChooseTaskDialog, ExportResultDialog, SaveMemoryDialog
from cdw.ui.result_tabs import QueryResultsTabs
from cdw.ui.preset_dialog import PresetDialog
from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import ButtonVariant, TextRole
from cdw.ui.widgets import CdwButton, CdwComboBox, CdwLabel, CdwMenu, CdwSqlEditor, CdwSection, CdwSectionGroup, CdwSectionSplitter, CdwTreeWidget

from cdw.ui.pages.base import BasePage, PageId, PageSpec


class SqlPage(BasePage):

    SCHEMA_SPLIT_SIZES = (360, 720)
    QUERY_SPLIT_SIZES = (220, 380)
    QUERY_SHORTCUT = "Ctrl+Return"
    CATALOG_COLUMNS = (tr('common.type'), tr('common.name'), tr('common.updated'))
    CATALOG_TYPE_LABELS = {StorageMode.FILE: tr('storage.file'), StorageMode.MEMORY: tr('storage.memory')}
    PRESET_STORAGE_ICONS = {StorageMode.FILE: IconName.FOLDER, StorageMode.MEMORY: IconName.MEMORY}
    SHARE_ACTIONS = ((tr('share.copy_text'), CopyFormat.TEXT), (tr('share.copy_html'), CopyFormat.HTML),
                     (tr('share.export_csv'), ExportFormat.CSV), (tr('share.export_json'), ExportFormat.JSON),
                     (tr('share.export_duckdb'), ExportFormat.DUCKDB))
    PREVIEW_SQL = "SELECT * FROM {table};"
    TEXT = {
        'cancel': tr('common.cancel'),
        'cancel_requested': tr('ui.pages.sql.text.cancel_requested'),
        'catalog': tr('ui.pages.sql.text.catalog'),
        'collect': tr('ui.pages.sql.text.collect'),
        'collected': tr('ui.pages.sql.text.collected'),
        'copied': tr('ui.pages.sql.text.copied'),
        'edit': tr('common.edit'),
        'editor_section': tr('ui.pages.sql.text.editor_section'),
        'exported': tr('ui.pages.sql.text.exported'),
        'memory_saved': tr('ui.pages.sql.text.memory_saved'),
        'new': tr('ui.pages.sql.text.new'),
        'no_preset': tr('ui.pages.sql.text.no_preset'),
        'no_tasks': tr('ui.pages.sql.text.no_tasks'),
        'preset_section': tr('common.preset'),
        'query': tr('ui.pages.sql.text.query'),
        'query_done': tr('ui.pages.sql.text.query_done'),
        'refresh_catalog': tr('ui.pages.sql.text.refresh_catalog'),
        'result_empty': tr('ui.pages.sql.text.result_empty'),
        'result_section': tr('ui.pages.sql.text.result_section'),
        'run_task': tr('pages.tasks.title'),
        'save_table': tr('ui.pages.sql.text.save_table'),
        'saved': tr('ui.pages.sql.text.saved'),
        'share': tr('ui.pages.sql.text.share'),
        'sql': tr('pages.sql.title'),
        'sql_hint': tr('ui.pages.sql.text.sql_hint'),
        'sql_subtitle': tr('ui.pages.sql.text.sql_subtitle'),
        'task_done': tr('ui.pages.sql.text.task_done'),
        'working': tr('ui.pages.sql.text.working'),
    }

    SPEC = PageSpec(PageId.SQL, TEXT["sql"], TEXT["sql"], TEXT["sql_subtitle"], IconName.DATABASE)
    presets_changed = Signal()

    def __init__(self, service, task_service, controller, parent=None, *, theme=None):
        self.service = service
        self.task_service = task_service
        self.controller = controller
        self._presets = []
        super().__init__(parent, theme=theme)
        self.build_content()
        self.controller.completed.connect(self._completed)
        self.controller.state_changed.connect(self._set_busy)
        self.query_shortcut = QShortcut(QKeySequence(SqlPage.QUERY_SHORTCUT), self)
        self.query_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.query_shortcut.activated.connect(self.run_query)
        self._set_busy()

    @property
    def selected_preset_id(self):
        return self.preset_combo.currentData()

    def build_content(self):
        layout = self.content
        sections = CdwSectionGroup()
        card = CdwSection(SqlPage.TEXT["preset_section"])
        box = card.content
        self.preset_combo = CdwComboBox()
        self.preset_combo.setObjectName("preset_selector")
        self.preset_combo.currentIndexChanged.connect(self._preset_selected)
        self.new_button = CdwButton(SqlPage.TEXT["new"], icon=IconName.ADD)
        self.new_button.setObjectName("new_preset")
        self.new_button.clicked.connect(self.create_preset)
        self.edit_button = CdwButton(SqlPage.TEXT["edit"], icon=IconName.EDIT)
        self.edit_button.clicked.connect(self.edit_preset)
        self.collect_button = CdwButton(SqlPage.TEXT["collect"], variant=ButtonVariant.PRIMARY, icon=IconName.PLAY)
        self.collect_button.setObjectName("collect")
        self.collect_button.clicked.connect(self.collect)
        box.addWidget(self.preset_combo)
        for button in (self.new_button, self.edit_button, self.collect_button):
            card.header.addWidget(button)
        sections.add_section(card)

        split = CdwSectionSplitter(Qt.Horizontal)
        catalog = CdwSection(SqlPage.TEXT["catalog"])
        self.refresh_tables_button = CdwButton("", icon=IconName.REFRESH)
        self.refresh_tables_button.setToolTip(SqlPage.TEXT["refresh_catalog"])
        self.refresh_tables_button.setAccessibleName(SqlPage.TEXT["refresh_catalog"])
        self.refresh_tables_button.clicked.connect(self.refresh_tables)
        catalog.header.addWidget(self.refresh_tables_button)
        self.schema_tree = CdwTreeWidget()
        self.schema_tree.setHeaderLabels(SqlPage.CATALOG_COLUMNS)
        self.schema_tree.setRootIsDecorated(True)
        self.schema_tree.setTreePosition(1)
        self.schema_tree.setProperty("cdwInsetTree", True)
        self.schema_tree.header().setStretchLastSection(False)
        self.schema_tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.schema_tree.itemDoubleClicked.connect(self._table_selected)
        catalog.content.addWidget(self.schema_tree)
        split.addWidget(catalog)
        query_split = CdwSectionSplitter(Qt.Vertical)
        editor_panel = CdwSection(SqlPage.TEXT["editor_section"])
        editor_layout = editor_panel.content
        self.sql_editor = CdwSqlEditor()
        self.sql_editor.setObjectName("sql_editor")
        self.sql_editor.setPlaceholderText(SqlPage.TEXT["sql_hint"])
        editor_layout.addWidget(self.sql_editor, 1)
        self.cancel_button = CdwButton(SqlPage.TEXT["cancel"], icon=IconName.STOP)
        self.cancel_button.setObjectName("cancel")
        self.cancel_button.clicked.connect(self.cancel)
        self.query_button = CdwButton(SqlPage.TEXT["query"], variant=ButtonVariant.PRIMARY, icon=IconName.PLAY)
        self.query_button.setObjectName("query")
        self.query_button.clicked.connect(self.run_query)
        editor_panel.header.addWidget(self.cancel_button)
        editor_panel.header.addWidget(self.query_button)
        query_split.addWidget(editor_panel)
        result_panel = CdwSection(SqlPage.TEXT["result_section"])
        result_layout = result_panel.content
        self.share_button = CdwButton(SqlPage.TEXT["share"], icon=IconName.DOCUMENT)
        self.task_button = CdwButton(SqlPage.TEXT["run_task"], icon=IconName.TASKS)
        self.save_table_button = CdwButton(SqlPage.TEXT["save_table"], icon=IconName.DATABASE)
        self.share_button.clicked.connect(self.share_result)
        self.task_button.clicked.connect(self.run_result_task)
        self.save_table_button.clicked.connect(self.save_result_table)
        for button in (self.share_button, self.task_button, self.save_table_button):
            result_panel.header.addWidget(button)
        self.result_empty = CdwLabel(SqlPage.TEXT["result_empty"], role=TextRole.MUTED)
        self.result_empty.setWordWrap(True)
        result_layout.addWidget(self.result_empty)
        self.result_tabs = QueryResultsTabs(theme=self.theme)
        self.result_tabs.setObjectName("query_results_tabs")
        self.result_tabs.currentChanged.connect(self._result_changed)
        self.result_tabs.closeRequested.connect(self.close_result_tab)
        result_layout.addWidget(self.result_tabs, 1)
        query_split.addWidget(result_panel)
        query_split.setSizes(SqlPage.QUERY_SPLIT_SIZES)
        split.addWidget(query_split)
        split.setStretchFactor(1, 1)
        split.setSizes(SqlPage.SCHEMA_SPLIT_SIZES)
        sections.add_section(split, 1)
        layout.addWidget(sections, 1)
        self.theme.changed.connect(self._style_catalog)
        self._style_catalog(self.theme.current)


    def _style_catalog(self, style):
        metrics = QFontMetrics(self.schema_tree.font())
        padding = 2 * style.metric("padding_y")
        type_width = max(metrics.horizontalAdvance(text) for text in SqlPage.CATALOG_TYPE_LABELS.values())
        time_width = metrics.horizontalAdvance(format_time(datetime.now().isoformat()))
        self.schema_tree.setColumnWidth(0, max(style.metric("catalog_type_width"), type_width + padding))
        self.schema_tree.setColumnWidth(2, max(style.metric("catalog_time_width"), time_width + padding))


    def refresh_presets(self, selected_id: str | None = None) -> None:
        selected_id = selected_id or self.selected_preset_id
        self._presets = self.service.list_presets()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for preset in self._presets:
            self.preset_combo.add_icon_item(preset.name, SqlPage.PRESET_STORAGE_ICONS[preset.storage.mode], preset.id)
        if not self._presets:
            self.preset_combo.addItem(SqlPage.TEXT["no_preset"], None)
        index = self.preset_combo.findData(selected_id)
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)
        self.preset_combo.blockSignals(False)
        self.preset_combo.setToolTip(self.preset_combo.currentText())
        self.presets_changed.emit()
        self._preset_selected()


    def _preset_selected(self) -> None:
        if self.controller.busy:
            return
        preset_id = self.selected_preset_id
        if preset_id:
            preset = self.service.get_preset(preset_id)
            self.sql_editor.setPlainText(SqlPage.PREVIEW_SQL.format(table=preset.qualified_table))
        self.controller.context()
        self._set_busy()


    def create_preset(self) -> None:
        self._open_preset_dialog()


    def edit_preset(self) -> None:
        if self.selected_preset_id:
            self._open_preset_dialog(self.service.get_preset(self.selected_preset_id))


    def _open_preset_dialog(self, preset=None) -> None:
        if self.controller.busy:
            return
        dialog = PresetDialog(self.service, preset, self)
        if dialog.exec() and dialog.saved_preset:
            self.log(SqlPage.TEXT["saved"].format(name=dialog.saved_preset.name))
            self.refresh_presets(dialog.saved_preset.id)


    def collect(self) -> None:
        if self.selected_preset_id and self.controller.collect(self.selected_preset_id):
            self.log(SqlPage.TEXT["working"])
            self._set_busy()


    def run_query(self) -> None:
        if self.controller.query(self.selected_preset_id, self.sql_editor.toPlainText()):
            self.log(SqlPage.TEXT["working"])
            self._set_busy()


    def refresh_tables(self) -> None:
        if self.controller.context():
            self._set_busy()


    def _result_changed(self, *_):
        self.result_empty.setVisible(self.result_tabs.count() == 0)
        self._set_busy()


    def close_result_tab(self, index):
        if self.controller.busy or index < 0:
            return
        pane = self.result_tabs.widget(index)
        if pane is not None and self.controller.release_result(pane.snapshot.id):
            self.result_tabs.removeTab(index)
            pane.deleteLater()
            self._result_changed()


    def share_result(self):
        if self.controller.busy or self.result_tabs.current_pane() is None:
            return
        menu = CdwMenu(self, theme=self.theme)
        choices = {}
        for index, (title, kind) in enumerate(SqlPage.SHARE_ACTIONS):
            if index == 2:
                menu.addSeparator()
            choices[menu.addAction(title)] = kind
        action = menu.exec(self.share_button.mapToGlobal(self.share_button.rect().bottomLeft()))
        kind = choices.get(action)
        menu.deleteLater()
        if kind in (CopyFormat.TEXT, CopyFormat.HTML):
            self.copy_result(kind)
        elif kind is not None:
            self.export_result(kind)


    def copy_result(self, format):
        pane = self.result_tabs.current_pane()
        if pane is not None and not self.controller.busy:
            if self.controller.copy_result(pane.snapshot.id, format, pane.row_indices()):
                self.log(SqlPage.TEXT["working"])
                self._set_busy()


    def export_result(self, format):
        pane = self.result_tabs.current_pane()
        if pane is None or self.controller.busy:
            return
        dialog = ExportResultDialog(format, self.result_tabs.tabText(self.result_tabs.currentIndex()),
                                    self.service.config.data_directory, self, theme=self.theme)
        if dialog.exec():
            if self.controller.export_result(pane.snapshot.id, dialog.path, dialog.format,
                                             dialog.delimiter, pane.row_indices()):
                self.log(SqlPage.TEXT["working"])
                self._set_busy()
        dialog.deleteLater()


    def save_result_table(self):
        pane = self.result_tabs.current_pane()
        if pane is None or self.controller.busy:
            return
        dialog = SaveMemoryDialog(self.result_tabs.tabText(self.result_tabs.currentIndex()), self, theme=self.theme)
        if dialog.exec():
            if self.controller.save_result_table(pane.snapshot.id, dialog.table_name, pane.row_indices()):
                self.log(SqlPage.TEXT["working"])
                self._set_busy()
        dialog.deleteLater()


    def run_result_task(self):
        pane = self.result_tabs.current_pane()
        if pane is None or self.controller.busy:
            return
        try:
            tasks = self.task_service.list_tasks()
        except CdwError as error:
            self.error_requested.emit(error)
            return
        if not tasks:
            self.navigate_requested.emit(PageId.TASKS)
            self.log(SqlPage.TEXT["no_tasks"])
            return
        dialog = ChooseTaskDialog(tasks, self, theme=self.theme)
        if dialog.exec():
            result = pane.current_result()
            if self.controller.run_task(dialog.task_id, result):
                self.log(SqlPage.TEXT["working"])
                self._set_busy()
        dialog.deleteLater()


    def _set_busy(self, *_args) -> None:
        busy = self.controller.busy
        selected = bool(self.selected_preset_id)
        self.new_button.setEnabled(not busy)
        self.preset_combo.setEnabled(not busy)
        self.edit_button.setEnabled(selected and not busy)
        self.collect_button.setEnabled(selected and not busy)
        self.query_button.setEnabled(not busy)
        self.sql_editor.setReadOnly(busy)
        self.schema_tree.setEnabled(not busy)
        self.refresh_tables_button.setEnabled(not busy)
        available = self.result_tabs.current_pane() is not None and not busy
        for button in (self.share_button, self.task_button, self.save_table_button):
            button.setEnabled(available)
        self.cancel_button.setEnabled(self.controller.state.can_cancel)


    def _render_schema(self, tables) -> None:
        self.schema_tree.clear()
        for table in tables:
            kind = SqlPage.CATALOG_TYPE_LABELS[table.storage_mode]
            item = QTreeWidgetItem([kind, table.display_name, format_time(table.updated_at) if table.updated_at else EMPTY_VALUE])
            item.setData(0, Qt.UserRole, table.name)
            item.setToolTip(1, f"{table.name}\n{table.storage_key}")
            if table.updated_at:
                item.setToolTip(2, table.updated_at)
            for name, dtype in table.columns:
                child = QTreeWidgetItem([dtype, name, ""])
                child.setToolTip(0, dtype)
                child.setToolTip(1, name)
                item.addChild(child)
            self.schema_tree.addTopLevelItem(item)
            item.setExpanded(False)


    def _table_selected(self, item, column) -> None:
        if not self.controller.busy and (table := item.data(0, Qt.UserRole)):
            self.sql_editor.setPlainText(SqlPage.PREVIEW_SQL.format(table=table))


    def _completed(self, operation: str, value) -> None:
        if not self.controller.accepting:
            return
        if operation == Operation.BOOT:
            self.refresh_presets()
        elif operation == Operation.CONTEXT:
            self._render_schema(value)
        elif operation == Operation.COLLECT:
            result, tables = value
            self._render_schema(tables)
            self.log(SqlPage.TEXT["collected"].format(inserted=result.inserted_rows, total=result.total_rows, seconds=result.elapsed_seconds))
            if result.stderr:
                self.log_details_requested.emit(result.stderr)
        elif operation == Operation.QUERY:
            self.result_tabs.add_snapshot(value)
            self._result_changed()
            self.log(SqlPage.TEXT["query_done"])
        elif operation == Operation.COPY:
            text, html = value
            mime = QMimeData()
            mime.setText(text)
            if html is not None:
                mime.setHtml(html)
            QApplication.clipboard().setMimeData(mime)
            self.log(SqlPage.TEXT["copied"])
        elif operation == Operation.EXPORT:
            self.log(SqlPage.TEXT["exported"].format(path=value))
        elif operation == Operation.SAVE_TABLE:
            table, tables = value
            self._render_schema(tables)
            self.log(SqlPage.TEXT["memory_saved"].format(table=table.name))
        self._set_busy()


    def cancel(self):
        if self.controller.cancel():
            self.log(SqlPage.TEXT["cancel_requested"])

    def log(self, message):
        self.log_requested.emit(message)
