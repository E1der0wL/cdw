"""A reusable, nonmodal view of the current session's log."""

from cdw.i18n import tr

from qtpy.QtCore import Qt

from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import LayoutRole, TextRole
from cdw.ui.widgets import CdwButton, CdwDialog, CdwLabel, CdwPlainTextEdit, CdwVBoxLayout


class LogDialog(CdwDialog):

    LOG_MAXIMUM_BLOCKS = 1500
    TEXT = {
        "title": tr('ui.log_dialog.text.title'),
        "retention": tr('ui.log_dialog.text.retention'),
        "empty": tr('ui.log_dialog.text.empty'),
        "close": tr('common.close'),
    }

    def __init__(self, parent=None, *, theme=None):
        super().__init__(parent, theme=theme)
        self.setObjectName("full_log_dialog")
        self.setWindowTitle(LogDialog.TEXT["title"])
        self.setModal(False)
        content = CdwVBoxLayout(self, role=LayoutRole.DIALOG, theme=self.theme)
        content.addWidget(CdwLabel(LogDialog.TEXT["retention"].format(count=LogDialog.LOG_MAXIMUM_BLOCKS),
                                   role=TextRole.MUTED, theme=self.theme))
        self.log_view = CdwPlainTextEdit(theme=self.theme)
        self.log_view.setObjectName("full_log_view")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(LogDialog.LOG_MAXIMUM_BLOCKS)
        self.log_view.setPlaceholderText(LogDialog.TEXT["empty"])
        content.addWidget(self.log_view, 1)
        self.close_button = CdwButton(LogDialog.TEXT["close"], icon=IconName.CLOSE, theme=self.theme)
        self.close_button.clicked.connect(self.close)
        content.addWidget(self.close_button, alignment=Qt.AlignRight)

    def reveal(self):
        self.show()
        self.raise_()
        self.activateWindow()
