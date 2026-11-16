from __future__ import annotations

from datetime import datetime
from importlib.metadata import version

from qtpy.QtCore import QMimeData, Qt, QTimer
from qtpy.QtGui import QFontMetrics, QKeySequence, QShortcut
from qtpy.QtWidgets import QApplication, QButtonGroup, QHeaderView, QTableWidgetItem, QTreeWidgetItem

from cdb import __version__
from cdb.branding import APP_FULL_NAME, APP_NAME
from cdb.application.service import ApplicationService
from cdb.application.tasks import TaskService
from cdb.config import BYTES_PER_MIB, DEFAULT_HISTORY_LIMIT
from cdb.domain.contracts import CdbError, ErrorCode, RunState, StorageMode
from cdb.ui.controller import Controller, Operation, PREVIEW_SQL
from cdb.domain.workspace import ExportFormat
from cdb.ui.result_actions import ChooseTaskDialog, ExportResultDialog, SaveMemoryDialog
from cdb.ui.result_tabs import QueryResultsTabs
from cdb.ui.tasks_page import TasksPage
from cdb.ui.log_dialog import LogDialog
from cdb.ui.preset_dialog import PresetDialog
from cdb.ui.design.manager import ThemeManager
from cdb.ui.design.icons import IconName
from cdb.ui.design.spec import ButtonVariant, LayoutRole, TextRole
from cdb.ui.settings_page import SettingsPage
from cdb.ui.widgets import (
    CdbButton, CdbComboBox, CdbHBoxLayout, CdbLabel, CdbLogBar,
    CdbMainWindow, CdbMenu, CdbMessageBox, CdbNavigationButton, CdbPage, CdbScrollArea, CdbSqlEditor,
    CdbSection, CdbSectionGroup, CdbSectionSplitter, CdbSidebar, CdbSidebarSplitter, CdbStackedWidget, CdbTableWidget,
    CdbTitleArea, CdbTreeWidget, CdbVBoxLayout, CdbWidget, LABEL_COMPATIBILITY_ROLES,
)

PAGE_HOME, PAGE_SQL, PAGE_SCHEDULER, PAGE_TASKS, PAGE_SETTINGS, PAGE_PROFILE = range(6)
NAVIGATION = ((PAGE_HOME, "홈"), (PAGE_SQL, "SQL 워크스페이스"), (PAGE_SCHEDULER, "스케줄러"), (PAGE_TASKS, "작업"))
BOTTOM_NAVIGATION = ((PAGE_SETTINGS, "설정"), (PAGE_PROFILE, "프로필"))
TIME_FORMAT = "%m-%d %H:%M:%S"
STATUS_TIME_FORMAT = "%H:%M:%S"
EMPTY_VALUE = "—"
SCHEMA_SPLIT_SIZES = (360, 720)
QUERY_SPLIT_SIZES = (220, 380)
NAV_ICONS = {PAGE_HOME: IconName.HOME, PAGE_SQL: IconName.DATABASE, PAGE_SCHEDULER: IconName.SCHEDULE,
             PAGE_TASKS: IconName.TASKS, PAGE_SETTINGS: IconName.SETTINGS, PAGE_PROFILE: IconName.PROFILE}
QUERY_SHORTCUT = "Ctrl+Return"
HISTORY_COLUMNS = ("프리셋", "결과", "시작 시각", "저장 행", "소요 시간", "실행 ID")
CATALOG_COLUMNS = ("타입", "이름", "업데이트")
CATALOG_TYPE_LABELS = {StorageMode.FILE: "파일", StorageMode.MEMORY: "메모리"}
PRESET_STORAGE_ICONS = {StorageMode.FILE: IconName.FOLDER, StorageMode.MEMORY: IconName.MEMORY}
SHARE_ACTIONS = (("텍스트로 복사", "text"), ("HTML로 복사", "html"),
                 ("CSV 파일로 내보내기…", ExportFormat.CSV), ("JSON 파일로 내보내기…", ExportFormat.JSON),
                 ("DuckDB 파일로 내보내기…", ExportFormat.DUCKDB))
STAGE_LABELS = {
    RunState.RUNNING: "실행 중", RunState.PARSING: "파싱 중", RunState.SAVING: "저장 중",
    RunState.SUCCEEDED: "성공", RunState.FAILED: "실패", RunState.CANCELLED: "취소", RunState.INTERRUPTED: "중단",
}
TEXT = {
    "title": f"{APP_NAME} · {APP_FULL_NAME}", "brand": APP_NAME, "caption": APP_FULL_NAME.upper(),
    "home": "명령어에서 데이터로", "home_subtitle": "프리셋을 등록하고, 결과를 모으고, SQL로 확인하세요.",
    "presets": "등록된 프리셋", "runs": "최근 실행", "last": "최근 실행 결과", "history": "실행 이력 · 최근 {count}개까지",
    "empty_history": "실행 이력이 없습니다. 프리셋 등록 후 ‘지금 수집’을 눌러 시작하세요.",
    "open_sql": "SQL 워크스페이스 열기", "sql": "SQL 워크스페이스", "sql_subtitle": "명령어 출력 → DuckDB → SQL 조회",
    "new": "프리셋 등록", "edit": "편집", "collect": "지금 수집", "no_preset": "등록된 프리셋이 없습니다",
    "preset_section": "프리셋", "editor_section": "SQL 편집기", "result_section": "조회 결과",
    "sql_hint": "SELECT 문 하나를 입력하세요. Ctrl+Enter로 실행합니다.",
    "query": "SQL 실행", "cancel": "취소", "result_empty": "SQL을 실행하면 결과 탭이 만들어집니다.",
    "share": "공유", "run_task": "작업", "save_table": "저장", "catalog": "사용 가능한 테이블",
    "refresh_catalog": "테이블 목록 새로고침", "copied": "현재 결과 테이블을 클립보드에 복사했습니다.",
    "exported": "조회 결과를 내보냈습니다. · {path}", "memory_saved": "메모리 테이블 저장 완료 · {table}",
    "no_tasks": "작업을 먼저 등록해 주세요. 입력 파일 경로는 명령어 인자의 {input}으로 전달됩니다.",
    "task_done": "작업 완료 · {name} · {seconds:.2f}초",
    "collected": "수집 완료 · {inserted:,}행 저장 · 현재 {total:,}행 · {seconds:.2f}초",
    "saved": "프리셋 저장 완료 · {name}", "query_done": "SQL 조회 완료", "working": "처리 중…", "ready": "준비됨",
    "cancel_requested": "취소를 요청했습니다. 현재 처리가 끝날 때까지 기다려 주세요.",
    "committing": "저장 트랜잭션 처리 중 · 완료 후 결과를 확인합니다.",
    "log_hint": "클릭하거나 Enter·Space를 눌러 전체 로그 보기",
    "close_title": "진행 중인 처리", "close_question": "진행 중인 실행을 취소하고 종료할까요? 이미 시작한 DB 저장은 완료 후 종료합니다.",
    "details": "실행 상세", "detail_template": "실행 ID: {id}\n상태: {state}\n저장 대상: {storage}\n테이블: {table}\n\n{details}\n\nstderr:\n{stderr}",
    "scheduler": "스케줄러", "scheduler_body": "특정 시각과 반복 간격에 따른 자동 수집은 후속 버전에서 제공합니다.\n현재 버전에서는 SQL 워크스페이스의 ‘지금 수집’을 이용하세요.",
    "later": "후속 버전",
    "settings_values": "데이터 폴더\n{directory}\n\n기본 실행 제한\n{timeout:g}초 / stdout + stderr {mib} MiB\n\nSQL 조회\n결과 탭에 전체 결과 보관 · CLI 미리보기 최대 {rows:,}행\n\nGUI\nQtPy + PySide6 {qt_version} · {app_name} {version}",
    "profile": "프로필", "profile_body": "현재 버전은 로컬 단일 사용자 작업 환경을 사용합니다.\n별도의 로그인이나 원격 서버 연결은 필요하지 않습니다.",
    "profile_values": "작업 환경\n{directory}\n\n프리셋과 실행 이력은 metadata.sqlite에 저장됩니다.\n메모리 데이터는 현재 앱 프로세스에만 존재합니다.",
}


def format_time(value: str | None) -> str:
    return datetime.fromisoformat(value).astimezone().strftime(TIME_FORMAT) if value else EMPTY_VALUE


def label(text: str, name: str | None = None) -> CdbLabel:
    return CdbLabel(text, role=LABEL_COMPATIBILITY_ROLES.get(name, TextRole.BODY))


class MainWindow(CdbMainWindow):
    def __init__(self, service: ApplicationService) -> None:
        super().__init__(theme=ThemeManager.for_service(service))
        self.service = service
        self.task_service = TaskService(service.config)
        self.controller = Controller(service)
        self._closing = False
        self._closed = False
        self._saving = False
        self._presets = []
        self._history_runs = []
        self.setWindowTitle(TEXT["title"])
        self._build()
        self.controller.completed.connect(self._completed, Qt.QueuedConnection)
        self.controller.failed.connect(self._failed, Qt.QueuedConnection)
        self.controller.progress.connect(self._progress, Qt.QueuedConnection)
        self.controller.submit(Operation.BOOT, self.service.recover)
        self._set_busy()
        if self.theme.load_warning:
            self.log(self.theme.load_warning)

    @property
    def selected_preset_id(self) -> str | None:
        return self.preset_combo.currentData()

    @property
    def result_model(self):
        pane = self.result_tabs.current_pane()
        return pane.source_model if pane is not None else None

    @property
    def result_table(self):
        pane = self.result_tabs.current_pane()
        return pane.view if pane is not None else None

    def _build(self) -> None:
        root = CdbWidget(surface="window")
        layout = CdbHBoxLayout(root, role=LayoutRole.FLUSH)
        sidebar = CdbSidebar()
        self.sidebar = sidebar
        sidebar_layout = CdbVBoxLayout(sidebar, role=LayoutRole.FLUSH)
        self.sidebar_title = CdbTitleArea(TEXT["brand"], TEXT["caption"], title_role=TextRole.BRAND,
                                         subtitle_role=TextRole.CAPTION, theme=self.theme)
        self.sidebar_title.header.addWidget(sidebar.toggle_button)
        sidebar_layout.addWidget(self.sidebar_title)
        sidebar.register_expanded_widget(self.sidebar_title.heading)
        sidebar.register_expanded_widget(self.sidebar_title.description)
        nav = CdbVBoxLayout(role=LayoutRole.SIDEBAR)
        sidebar_layout.addLayout(nav, 1)
        self.nav_group = QButtonGroup(self)
        self.nav_buttons = {}
        for index, title in NAVIGATION:
            self._nav_button(nav, index, title)
        nav.addStretch()
        for index, title in BOTTOM_NAVIGATION:
            self._nav_button(nav, index, title)
        footer = label(f"v{__version__}  ·  LOCAL", "brandCaption")
        nav.addWidget(footer)
        sidebar.register_expanded_widget(footer)
        sidebar.apply_style(self.theme.current)
        self.pages = CdbStackedWidget()
        self.pages.addWidget(self._scroll(self._home_page()))
        self.pages.addWidget(self._scroll(self._sql_page()))
        self.pages.addWidget(self._scroll(self._info_page(TEXT["scheduler"], TEXT["scheduler_body"], TEXT["later"])))
        self.tasks_page = TasksPage(self.task_service, theme=self.theme)
        self.pages.addWidget(self._scroll(self.tasks_page))
        config = self.service.config
        self.settings_page = SettingsPage(TEXT["settings_values"].format(
            directory=config.data_directory, timeout=config.execution_policy.timeout_seconds,
            mib=config.execution_policy.max_output_bytes // BYTES_PER_MIB,
            rows=config.query_policy.preview_rows, qt_version=version("PySide6"), app_name=APP_NAME, version=__version__), theme=self.theme)
        self.pages.addWidget(self._scroll(self.settings_page))
        self.pages.addWidget(self._scroll(self._info_page(TEXT["profile"], TEXT["profile_body"], TEXT["profile_values"].format(directory=config.data_directory))))
        self.nav_group.idClicked.connect(self.pages.setCurrentIndex)
        self.nav_buttons[PAGE_HOME].setChecked(True)
        self.shell_splitter = CdbSidebarSplitter(sidebar, self.pages, theme=self.theme)
        layout.addWidget(self.shell_splitter)
        self.setCentralWidget(root)

        self.log_dialog = LogDialog(self, theme=self.theme)
        self.log_view = self.log_dialog.log_view
        self.log_bar = CdbLogBar(TEXT["ready"], self, theme=self.theme)
        self.log_bar.setToolTip(TEXT["log_hint"])
        self.log_bar.setAccessibleName(TEXT["log_hint"])
        self.log_bar.activated.connect(self.log_dialog.reveal)
        self.setStatusBar(self.log_bar)
        self.status_label = self.log_bar.message_label
        self.query_shortcut = QShortcut(QKeySequence(QUERY_SHORTCUT), self)
        self.query_shortcut.activated.connect(self.run_query)

    def _nav_button(self, layout, index, title) -> None:
        button = CdbNavigationButton(title, icon=NAV_ICONS[index])
        self.sidebar.register_navigation(button)
        self.nav_group.addButton(button, index)
        self.nav_buttons[index] = button
        layout.addWidget(button)

    def _scroll(self, page):
        scroll = CdbScrollArea()
        scroll.setWidget(page)
        return scroll

    def _page(self, title: str, subtitle: str):
        page = CdbPage(title, subtitle)
        return page, page.content

    def _home_page(self):
        page, layout = self._page(TEXT["home"], TEXT["home_subtitle"])
        sections = CdbSectionGroup()
        cards = CdbSectionGroup(Qt.Horizontal)
        self.metric_labels = []
        for title in (TEXT["presets"], TEXT["runs"], TEXT["last"]):
            card = CdbSection(title)
            content = card.content
            metric = label(EMPTY_VALUE, "metric")
            content.addWidget(metric)
            self.metric_labels.append(metric)
            cards.add_section(card, 1)
        sections.add_section(cards)
        open_button = CdbButton(TEXT["open_sql"], variant=ButtonVariant.PRIMARY, icon=IconName.DATABASE)
        open_button.clicked.connect(lambda: self.show_page(PAGE_SQL))
        history = CdbSection(TEXT["history"].format(count=DEFAULT_HISTORY_LIMIT))
        history.header.addWidget(open_button)
        self.history_table = CdbTableWidget(0, len(HISTORY_COLUMNS))
        self.history_table.setHorizontalHeaderLabels(HISTORY_COLUMNS)
        self.history_table.setSelectionBehavior(CdbTableWidget.SelectRows)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.cellDoubleClicked.connect(self._show_run)
        history.content.addWidget(self.history_table, 1)
        self.history_hint = label(TEXT["empty_history"], "muted")
        history.content.addWidget(self.history_hint)
        sections.add_section(history, 1)
        layout.addWidget(sections, 1)
        return page

    def _sql_page(self):
        page, layout = self._page(TEXT["sql"], TEXT["sql_subtitle"])
        sections = CdbSectionGroup()
        card = CdbSection(TEXT["preset_section"])
        box = card.content
        self.preset_combo = CdbComboBox()
        self.preset_combo.setObjectName("preset_selector")
        self.preset_combo.currentIndexChanged.connect(self._preset_selected)
        self.new_button = CdbButton(TEXT["new"], icon=IconName.ADD)
        self.new_button.setObjectName("new_preset")
        self.new_button.clicked.connect(self.create_preset)
        self.edit_button = CdbButton(TEXT["edit"], icon=IconName.EDIT)
        self.edit_button.clicked.connect(self.edit_preset)
        self.collect_button = CdbButton(TEXT["collect"], variant=ButtonVariant.PRIMARY, icon=IconName.PLAY)
        self.collect_button.setObjectName("collect")
        self.collect_button.clicked.connect(self.collect)
        box.addWidget(self.preset_combo)
        for button in (self.new_button, self.edit_button, self.collect_button):
            card.header.addWidget(button)
        sections.add_section(card)

        split = CdbSectionSplitter(Qt.Horizontal)
        catalog = CdbSection(TEXT["catalog"])
        self.refresh_tables_button = CdbButton("", icon=IconName.REFRESH)
        self.refresh_tables_button.setToolTip(TEXT["refresh_catalog"])
        self.refresh_tables_button.setAccessibleName(TEXT["refresh_catalog"])
        self.refresh_tables_button.clicked.connect(self.refresh_tables)
        catalog.header.addWidget(self.refresh_tables_button)
        self.schema_tree = CdbTreeWidget()
        self.schema_tree.setHeaderLabels(CATALOG_COLUMNS)
        self.schema_tree.setRootIsDecorated(True)
        self.schema_tree.setTreePosition(1)
        self.schema_tree.setProperty("cdbInsetTree", True)
        self.schema_tree.header().setStretchLastSection(False)
        self.schema_tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.schema_tree.itemDoubleClicked.connect(self._table_selected)
        catalog.content.addWidget(self.schema_tree)
        split.addWidget(catalog)
        query_split = CdbSectionSplitter(Qt.Vertical)
        editor_panel = CdbSection(TEXT["editor_section"])
        editor_layout = editor_panel.content
        self.sql_editor = CdbSqlEditor()
        self.sql_editor.setObjectName("sql_editor")
        self.sql_editor.setPlaceholderText(TEXT["sql_hint"])
        editor_layout.addWidget(self.sql_editor, 1)
        self.cancel_button = CdbButton(TEXT["cancel"], icon=IconName.STOP)
        self.cancel_button.setObjectName("cancel")
        self.cancel_button.clicked.connect(self.cancel)
        self.query_button = CdbButton(TEXT["query"], variant=ButtonVariant.PRIMARY, icon=IconName.PLAY)
        self.query_button.setObjectName("query")
        self.query_button.clicked.connect(self.run_query)
        editor_panel.header.addWidget(self.cancel_button)
        editor_panel.header.addWidget(self.query_button)
        query_split.addWidget(editor_panel)
        result_panel = CdbSection(TEXT["result_section"])
        result_layout = result_panel.content
        self.share_button = CdbButton(TEXT["share"], icon=IconName.DOCUMENT)
        self.task_button = CdbButton(TEXT["run_task"], icon=IconName.TASKS)
        self.save_table_button = CdbButton(TEXT["save_table"], icon=IconName.DATABASE)
        self.share_button.clicked.connect(self.share_result)
        self.task_button.clicked.connect(self.run_result_task)
        self.save_table_button.clicked.connect(self.save_result_table)
        for button in (self.share_button, self.task_button, self.save_table_button):
            result_panel.header.addWidget(button)
        self.result_empty = CdbLabel(TEXT["result_empty"], role=TextRole.MUTED)
        self.result_empty.setWordWrap(True)
        result_layout.addWidget(self.result_empty)
        self.result_tabs = QueryResultsTabs(theme=self.theme)
        self.result_tabs.setObjectName("query_results_tabs")
        self.result_tabs.currentChanged.connect(self._result_changed)
        self.result_tabs.closeRequested.connect(self.close_result_tab)
        result_layout.addWidget(self.result_tabs, 1)
        query_split.addWidget(result_panel)
        query_split.setSizes(QUERY_SPLIT_SIZES)
        split.addWidget(query_split)
        split.setStretchFactor(1, 1)
        split.setSizes(SCHEMA_SPLIT_SIZES)
        sections.add_section(split, 1)
        layout.addWidget(sections, 1)
        self.theme.changed.connect(self._style_catalog)
        self._style_catalog(self.theme.current)
        return page

    def _style_catalog(self, style):
        metrics = QFontMetrics(self.schema_tree.font())
        padding = 2 * style.metric("padding_y")
        type_width = max(metrics.horizontalAdvance(text) for text in CATALOG_TYPE_LABELS.values())
        time_width = metrics.horizontalAdvance(datetime.now().strftime(TIME_FORMAT))
        self.schema_tree.setColumnWidth(0, max(style.metric("catalog_type_width"), type_width + padding))
        self.schema_tree.setColumnWidth(2, max(style.metric("catalog_time_width"), time_width + padding))

    def _info_page(self, title, description, body):
        page, layout = self._page(title, description)
        text = CdbLabel(body, padding_role=LayoutRole.SECTION)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(text)
        layout.addStretch()
        return page

    def show_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)

    def refresh_presets(self, selected_id: str | None = None) -> None:
        selected_id = selected_id or self.selected_preset_id
        self._presets = self.service.list_presets()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for preset in self._presets:
            self.preset_combo.add_icon_item(preset.name, PRESET_STORAGE_ICONS[preset.storage.mode], preset.id)
        if not self._presets:
            self.preset_combo.addItem(TEXT["no_preset"], None)
        index = self.preset_combo.findData(selected_id)
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)
        self.preset_combo.blockSignals(False)
        self.preset_combo.setToolTip(self.preset_combo.currentText())
        self._refresh_history()
        self._preset_selected()

    def _preset_selected(self) -> None:
        if self.controller.busy:
            return
        preset_id = self.selected_preset_id
        if preset_id:
            preset = self.service.get_preset(preset_id)
            self.sql_editor.setPlainText(PREVIEW_SQL.format(table=preset.qualified_table))
        self.controller.context(preset_id)
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
            self.log(TEXT["saved"].format(name=dialog.saved_preset.name))
            self.refresh_presets(dialog.saved_preset.id)

    def collect(self) -> None:
        if self.selected_preset_id and self.controller.collect(self.selected_preset_id):
            self._saving = False
            self.log(TEXT["working"])
            self._set_busy()

    def run_query(self) -> None:
        if self.pages.currentIndex() != PAGE_SQL:
            return
        if self.controller.query(self.selected_preset_id, self.sql_editor.toPlainText()):
            self.log(TEXT["working"])
            self._set_busy()

    def refresh_tables(self) -> None:
        if self.controller.context(self.selected_preset_id):
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
        menu = CdbMenu(self, theme=self.theme)
        choices = {}
        for index, (title, kind) in enumerate(SHARE_ACTIONS):
            if index == 2:
                menu.addSeparator()
            choices[menu.addAction(title)] = kind
        action = menu.exec(self.share_button.mapToGlobal(self.share_button.rect().bottomLeft()))
        kind = choices.get(action)
        menu.deleteLater()
        if kind in ("text", "html"):
            self.copy_result(kind)
        elif kind is not None:
            self.export_result(kind)

    def copy_result(self, format):
        pane = self.result_tabs.current_pane()
        if pane is not None and not self.controller.busy:
            if self.controller.copy_result(pane.snapshot.id, format, pane.row_indices()):
                self.log(TEXT["working"])
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
                self.log(TEXT["working"])
                self._set_busy()
        dialog.deleteLater()

    def save_result_table(self):
        pane = self.result_tabs.current_pane()
        if pane is None or self.controller.busy:
            return
        dialog = SaveMemoryDialog(self.result_tabs.tabText(self.result_tabs.currentIndex()), self, theme=self.theme)
        if dialog.exec():
            if self.controller.save_result_table(pane.snapshot.id, dialog.table_name, pane.row_indices()):
                self.log(TEXT["working"])
                self._set_busy()
        dialog.deleteLater()

    def run_result_task(self):
        pane = self.result_tabs.current_pane()
        if pane is None or self.controller.busy:
            return
        try:
            tasks = self.task_service.list_tasks()
        except CdbError as error:
            self.log(f"{error}\n{error.details}".strip())
            self.log_dialog.reveal()
            return
        if not tasks:
            self.show_page(PAGE_TASKS)
            self.log(TEXT["no_tasks"])
            return
        dialog = ChooseTaskDialog(tasks, self, theme=self.theme)
        if dialog.exec():
            result = pane.current_result()
            if self.controller.run_task(self.task_service, dialog.task_id, result):
                self.log(TEXT["working"])
                self._set_busy()
        dialog.deleteLater()

    def cancel(self) -> None:
        if self.controller.busy and not self._saving:
            self.controller.cancel()
            self.log(TEXT["cancel_requested"])
            self.cancel_button.setEnabled(False)

    def _set_busy(self) -> None:
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
        self.tasks_page.set_busy(busy)
        self.cancel_button.setEnabled(busy and not self._saving and self.controller.operation in (Operation.COLLECT, Operation.QUERY, Operation.TASK))

    def _progress(self, progress) -> None:
        self._saving = progress.state is RunState.SAVING
        self.log(TEXT["committing"] if self._saving else progress.message)
        self._set_busy()

    def _completed(self, operation: str, value) -> None:
        self.controller.release()
        self._saving = False
        if self._closing:
            QTimer.singleShot(0, self.close)
            return
        if operation == Operation.BOOT:
            self.refresh_presets()
        elif operation == Operation.CONTEXT:
            self._render_schema(value)
        elif operation == Operation.COLLECT:
            result, tables = value
            self._render_schema(tables)
            self._refresh_history()
            self.log(TEXT["collected"].format(inserted=result.inserted_rows, total=result.total_rows, seconds=result.elapsed_seconds))
            if result.stderr:
                self.log_view.appendPlainText(result.stderr)
        elif operation == Operation.QUERY:
            self.result_tabs.add_snapshot(value)
            self._result_changed()
            self.log(TEXT["query_done"])
        elif operation == Operation.COPY:
            text, html = value
            mime = QMimeData()
            mime.setText(text)
            if html is not None:
                mime.setHtml(html)
            QApplication.clipboard().setMimeData(mime)
            self.log(TEXT["copied"])
        elif operation == Operation.EXPORT:
            self.log(TEXT["exported"].format(path=value))
        elif operation == Operation.SAVE_TABLE:
            table, tables = value
            self._render_schema(tables)
            self.log(TEXT["memory_saved"].format(table=table.name))
        elif operation == Operation.TASK:
            self.log(TEXT["task_done"].format(name=value.task_name, seconds=value.elapsed_seconds))
            for output in (value.stdout, value.stderr):
                if output:
                    self.log_view.appendPlainText(output)
        self._set_busy()

    def _failed(self, operation: str, error: CdbError) -> None:
        self.controller.release()
        self._saving = False
        self._refresh_history()
        self.log(f"{error}\n{error.details}".strip())
        if self._closing:
            QTimer.singleShot(0, self.close)
            return
        if error.code is not ErrorCode.CANCELLED:
            self.log_dialog.reveal()
        self._set_busy()

    def _render_schema(self, tables) -> None:
        self.schema_tree.clear()
        for table in tables:
            kind = CATALOG_TYPE_LABELS[table.storage_mode]
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
            self.sql_editor.setPlainText(PREVIEW_SQL.format(table=table))

    def _refresh_history(self) -> None:
        self._history_runs = self.service.list_runs()
        self.metric_labels[0].setText(str(len(self.service.list_presets())))
        self.metric_labels[1].setText(str(len(self._history_runs)))
        self.metric_labels[2].setText(STAGE_LABELS[self._history_runs[0].state] if self._history_runs else EMPTY_VALUE)
        self.history_hint.setVisible(not self._history_runs)
        self.history_table.setRowCount(len(self._history_runs))
        for row, run in enumerate(self._history_runs):
            values = (run.preset_name, STAGE_LABELS[run.state], format_time(run.started_at),
                      str(run.row_count) if run.row_count is not None else EMPTY_VALUE,
                      f"{run.elapsed_seconds:.2f}s" if run.elapsed_seconds is not None else EMPTY_VALUE, run.id)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(run.details or value)
                self.history_table.setItem(row, column, item)
        self.history_table.resizeColumnsToContents()

    def _show_run(self, row, column) -> None:
        run = self._history_runs[row]
        message = CdbMessageBox(self)
        message.setWindowTitle(TEXT["details"])
        message.setTextFormat(Qt.PlainText)
        message.setText(TEXT["detail_template"].format(id=run.id, state=STAGE_LABELS[run.state], storage=run.storage_key,
                                                     table=run.table_name, details=run.details, stderr=run.stderr))
        message.exec()

    def log(self, message: str) -> None:
        self.status_label.setText(message.splitlines()[0])
        self.status_label.setToolTip(message)
        self.log_view.appendPlainText(f"[{datetime.now().strftime(STATUS_TIME_FORMAT)}] {message}")

    def closeEvent(self, event) -> None:
        if self._closed:
            event.accept()
            return
        if self.controller.busy:
            if not self._closing:
                answer = CdbMessageBox.question(self, TEXT["close_title"], TEXT["close_question"])
                if answer != CdbMessageBox.Yes:
                    event.ignore()
                    return
                self._closing = True
                self.controller.cancel()
            event.ignore()
            return
        self.controller.shutdown()
        self.log_dialog.close()
        self._closed = True
        event.accept()
