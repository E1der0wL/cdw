from __future__ import annotations

from cdw.i18n import tr
from cdw.ui.formatting import format_time, EMPTY_VALUE


from qtpy.QtCore import Qt
from qtpy.QtWidgets import QTableWidgetItem

from cdw.config import DEFAULT_HISTORY_LIMIT
from cdw.domain.contracts import RunState
from cdw.ui.controller import Operation
from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import ButtonVariant, TextRole
from cdw.ui.widgets import CdwButton, CdwLabel, CdwMessageBox, CdwSection, CdwSectionGroup, CdwTableWidget

from cdw.ui.pages.base import BasePage, PageId, PageSpec


class HomePage(BasePage):

    HISTORY_COLUMNS = (tr('common.preset'), tr('common.result'), tr('ui.pages.home.history_columns_2'), tr('ui.pages.home.history_columns_3'), tr('ui.pages.home.history_columns_4'), tr('ui.pages.home.history_columns_5'))
    STAGE_LABELS = {
        RunState.RUNNING: tr('run.running'), RunState.PARSING: tr('run.parsing'), RunState.SAVING: tr('run.saving'),
        RunState.SUCCEEDED: tr('run.succeeded'), RunState.FAILED: tr('run.failed'), RunState.CANCELLED: tr('common.cancel'), RunState.INTERRUPTED: tr('run.interrupted'),
    }
    TEXT = {
        'detail_template': tr('ui.pages.home.text.detail_template'),
        'details': tr('ui.pages.home.text.details'),
        'empty_history': tr('ui.pages.home.text.empty_history'),
        'history': tr('ui.pages.home.text.history'),
        'home': tr('ui.pages.home.text.home'),
        'home_subtitle': tr('ui.pages.home.text.home_subtitle'),
        'last': tr('ui.pages.home.text.last'),
        'open_sql': tr('ui.pages.home.text.open_sql'),
        'presets': tr('ui.pages.home.text.presets'),
        'runs': tr('ui.pages.home.text.runs'),
    }

    SPEC = PageSpec(PageId.HOME, tr('pages.home.navigation'), TEXT["home"], TEXT["home_subtitle"], IconName.HOME)

    def __init__(self, service, controller, parent=None, *, theme=None):
        self.service = service
        self._history_runs = []
        super().__init__(parent, theme=theme)
        self.build_content()
        controller.completed.connect(self._completed)
        controller.failed.connect(self._failed)

    def on_enter(self):
        self.refresh()

    def _completed(self, operation, value):
        if operation in (Operation.BOOT, Operation.COLLECT):
            self.refresh()

    def _failed(self, operation, error):
        if operation == Operation.COLLECT:
            self.refresh()

    def build_content(self):
        layout = self.content
        sections = CdwSectionGroup()
        cards = CdwSectionGroup(Qt.Horizontal)
        self.metric_labels = []
        for title in (HomePage.TEXT["presets"], HomePage.TEXT["runs"], HomePage.TEXT["last"]):
            card = CdwSection(title)
            content = card.content
            metric = CdwLabel(EMPTY_VALUE, role=TextRole.METRIC)
            content.addWidget(metric)
            self.metric_labels.append(metric)
            cards.add_section(card, 1)
        sections.add_section(cards)
        open_button = CdwButton(HomePage.TEXT["open_sql"], variant=ButtonVariant.PRIMARY, icon=IconName.DATABASE)
        open_button.clicked.connect(lambda: self.navigate_requested.emit(PageId.SQL))
        history = CdwSection(HomePage.TEXT["history"].format(count=DEFAULT_HISTORY_LIMIT))
        history.header.addWidget(open_button)
        self.history_table = CdwTableWidget(0, len(HomePage.HISTORY_COLUMNS))
        self.history_table.setHorizontalHeaderLabels(HomePage.HISTORY_COLUMNS)
        self.history_table.setSelectionBehavior(CdwTableWidget.SelectRows)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.cellDoubleClicked.connect(self._show_run)
        history.content.addWidget(self.history_table, 1)
        self.history_hint = CdwLabel(HomePage.TEXT["empty_history"], role=TextRole.MUTED)
        history.content.addWidget(self.history_hint)
        sections.add_section(history, 1)
        layout.addWidget(sections, 1)


    def refresh(self) -> None:
        self._history_runs = self.service.list_runs()
        self.metric_labels[0].setText(str(len(self.service.list_presets())))
        self.metric_labels[1].setText(str(len(self._history_runs)))
        self.metric_labels[2].setText(HomePage.STAGE_LABELS[self._history_runs[0].state] if self._history_runs else EMPTY_VALUE)
        self.history_hint.setVisible(not self._history_runs)
        self.history_table.setRowCount(len(self._history_runs))
        for row, run in enumerate(self._history_runs):
            values = (run.preset_name, HomePage.STAGE_LABELS[run.state], format_time(run.started_at),
                      str(run.row_count) if run.row_count is not None else EMPTY_VALUE,
                      f"{run.elapsed_seconds:.2f}s" if run.elapsed_seconds is not None else EMPTY_VALUE, run.id)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(run.details or value)
                self.history_table.setItem(row, column, item)
        self.history_table.resizeColumnsToContents()


    def _show_run(self, row, column) -> None:
        run = self._history_runs[row]
        message = CdwMessageBox(self)
        message.setWindowTitle(HomePage.TEXT["details"])
        message.setTextFormat(Qt.PlainText)
        message.setText(HomePage.TEXT["detail_template"].format(id=run.id, state=HomePage.STAGE_LABELS[run.state], storage=run.storage_key,
                                                     table=run.table_name, details=run.details, stderr=run.stderr))
        message.exec()
