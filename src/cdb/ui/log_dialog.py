"""A reusable, nonmodal view of the current session's log."""

from qtpy.QtCore import Qt

from cdb.ui.design.icons import IconName
from cdb.ui.design.spec import LayoutRole, TextRole
from cdb.ui.widgets import CdbButton, CdbDialog, CdbLabel, CdbPlainTextEdit, CdbVBoxLayout

LOG_MAXIMUM_BLOCKS = 1500
TEXT = {
    "title": "전체 로그",
    "retention": "현재 실행 세션의 로그 · 최근 {count:,}줄까지 표시합니다.",
    "empty": "아직 기록된 로그가 없습니다.",
    "close": "닫기",
}


class LogDialog(CdbDialog):
    def __init__(self, parent=None, *, theme=None):
        super().__init__(parent, theme=theme)
        self.setObjectName("full_log_dialog")
        self.setWindowTitle(TEXT["title"])
        self.setModal(False)
        content = CdbVBoxLayout(self, role=LayoutRole.DIALOG, theme=self.theme)
        content.addWidget(CdbLabel(TEXT["retention"].format(count=LOG_MAXIMUM_BLOCKS),
                                   role=TextRole.MUTED, theme=self.theme))
        self.log_view = CdbPlainTextEdit(theme=self.theme)
        self.log_view.setObjectName("full_log_view")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(LOG_MAXIMUM_BLOCKS)
        self.log_view.setPlaceholderText(TEXT["empty"])
        content.addWidget(self.log_view, 1)
        self.close_button = CdbButton(TEXT["close"], icon=IconName.CLOSE, theme=self.theme)
        self.close_button.clicked.connect(self.close)
        content.addWidget(self.close_button, alignment=Qt.AlignRight)

    def reveal(self):
        self.show()
        self.raise_()
        self.activateWindow()
