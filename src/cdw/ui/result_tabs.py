"""One immutable query snapshot and independent table view per result tab."""

from cdw.i18n import tr

from qtpy.QtCore import Signal
from qtpy.QtWidgets import QTabBar

from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import ButtonVariant
from cdw.ui.result_table import ResultTablePane
from cdw.ui.widgets import CdwButton, CdwTabWidget, TABLE_MINIMUM_ROWS


class ResultTab(ResultTablePane):
    def __init__(self, snapshot, parent=None, *, theme=None):
        self.snapshot = snapshot
        super().__init__(snapshot.result, parent, theme=theme)
        self.view.set_embedded(True)


class ResultCloseButton(CdwButton):

    CLOSE_TEXT = tr('ui.result_tabs.close_text')

    def __init__(self, parent=None, *, theme=None):
        super().__init__("", parent, icon=IconName.CLOSE, variant=ButtonVariant.FIELD_ACTION, theme=theme)
        self.setToolTip(ResultCloseButton.CLOSE_TEXT)
        self.setAccessibleName(ResultCloseButton.CLOSE_TEXT)

    def apply_style(self, style):
        super().apply_style(style)
        size = style.metric("icon") + style.metric("gap_small")
        self.setFixedSize(size, size)


class QueryResultsTabs(CdwTabWidget):

    UNTITLED_RESULT = tr('ui.pages.sql.text.result_section')
    DUPLICATE_TITLE = "{title} ({number})"

    closeRequested = Signal(int)

    def __init__(self, *args, theme=None, **kwargs):
        self._title_counts = {}
        super().__init__(*args, theme=theme, **kwargs)
        self.setMovable(True)
        self.setUsesScrollButtons(True)

    def apply_style(self, style):
        super().apply_style(style)
        self.setMinimumHeight(style.metric("table_row") * TABLE_MINIMUM_ROWS + style.metric("control_height"))

    def add_snapshot(self, snapshot):
        title = snapshot.title or QueryResultsTabs.UNTITLED_RESULT
        number = self._title_counts.get(title, 0) + 1
        self._title_counts[title] = number
        label = title if number == 1 else QueryResultsTabs.DUPLICATE_TITLE.format(title=title, number=number)
        pane = ResultTab(snapshot, theme=self.theme)
        index = self.addTab(pane, label)
        close = ResultCloseButton(theme=self.theme)
        close.clicked.connect(lambda: self.closeRequested.emit(self.indexOf(pane)))
        self.tabBar().setTabButton(index, QTabBar.RightSide, close)
        self.setTabToolTip(index, snapshot.created_at)
        self.setCurrentIndex(index)
        return pane

    def current_pane(self):
        return self.currentWidget()
