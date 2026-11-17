from cdw.i18n import tr
from qtpy.QtWidgets import QTableWidgetItem
from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import ButtonVariant, LayoutRole, TextRole
from cdw.ui.widgets import (
    CdwButton, CdwComboBox, CdwDialog, CdwFormLayout, CdwHBoxLayout, CdwLabel,
    CdwLineEdit, CdwScrollArea, CdwSqlEditor, CdwTableWidget, CdwTitleArea, CdwVBoxLayout, CdwWidget,
)


class DesignPreviewDialog(CdwDialog):

    TEXT = {
        'preview_title': tr('ui.pages.settings.text.preview'),
        'preview_subtitle': tr('ui.pages.settings.text.preview_subtitle'),
        'primary': tr('ui.pages.settings.text.primary'),
        'secondary': tr('common.edit'),
        'danger': tr('common.delete'),
        'disabled': tr('ui.pages.settings.text.disabled'),
        'typography': tr('ui.pages.settings.text.typography'),
        'muted': tr('ui.pages.settings.text.muted'),
        'field': tr('ui.pages.settings.text.field'),
        'placeholder': tr('ui.pages.settings.text.placeholder'),
        'select': tr('ui.pages.settings.text.select'),
        'option': tr('ui.pages.settings.text.option'),
        'icons': tr('ui.pages.settings.text.icons'),
        'home': tr('pages.home.navigation'),
        'database': tr('ui.pages.settings.text.database'),
        'schedule': tr('ui.pages.settings.text.schedule'),
        'close': tr('common.close'),
        'code': "SELECT hostname, status\nFROM source.host_inventory;",
    }
    PREVIEW_HEADERS = ("hostname", "status", "cpu_percent")
    PREVIEW_ROWS = (("rhel-app-01", "healthy", "23.5"), ("rhel-db-01", "warning", "67.2"))
    PREVIEW_ICONS = (("home", IconName.HOME), ("database", IconName.DATABASE), ("schedule", IconName.SCHEDULE))

    def __init__(self, parent=None, *, theme=None):
        super().__init__(parent, theme=theme)
        self.setWindowTitle(DesignPreviewDialog.TEXT["preview_title"])
        self.resize(self.theme.current.metric("preview_width"), self.theme.current.metric("preview_height"))
        layout = CdwVBoxLayout(self, role=LayoutRole.DIALOG, theme=self.theme)
        scroll = CdwScrollArea(theme=self.theme)
        page = CdwWidget(theme=self.theme, surface="window")
        page_layout = CdwVBoxLayout(page, role=LayoutRole.PAGE, theme=self.theme)
        page_layout.addWidget(CdwTitleArea(DesignPreviewDialog.TEXT["preview_title"], DesignPreviewDialog.TEXT["preview_subtitle"], eyebrow=tr("pages.eyebrow"), theme=self.theme))
        scroll.setWidget(page)
        layout.addWidget(scroll)
        content = CdwVBoxLayout(role=LayoutRole.SECTION, theme=self.theme)
        page_layout.addLayout(content)
        content.addWidget(CdwLabel(DesignPreviewDialog.TEXT["typography"], theme=self.theme))
        content.addWidget(CdwLabel(DesignPreviewDialog.TEXT["muted"], role=TextRole.MUTED, theme=self.theme))
        buttons = CdwHBoxLayout(theme=self.theme)
        for key, variant, icon in (("primary", ButtonVariant.PRIMARY, IconName.PLAY),
                                   ("secondary", ButtonVariant.SECONDARY, IconName.EDIT),
                                   ("danger", ButtonVariant.DANGER, IconName.DELETE),
                                   ("disabled", ButtonVariant.SECONDARY, IconName.STOP)):
            button = CdwButton(DesignPreviewDialog.TEXT[key], variant=variant, icon=icon, theme=self.theme)
            button.setEnabled(key != "disabled")
            buttons.addWidget(button)
        content.addLayout(buttons)
        fields = CdwFormLayout(role=LayoutRole.FORM, theme=self.theme)
        entry = CdwLineEdit(theme=self.theme)
        entry.setPlaceholderText(DesignPreviewDialog.TEXT["placeholder"])
        choice = CdwComboBox(theme=self.theme)
        choice.addItem(DesignPreviewDialog.TEXT["option"])
        fields.addRow(DesignPreviewDialog.TEXT["field"], entry)
        fields.addRow(DesignPreviewDialog.TEXT["select"], choice)
        content.addLayout(fields)
        content.addWidget(CdwLabel(DesignPreviewDialog.TEXT["icons"], role=TextRole.MUTED, theme=self.theme))
        for key, icon in DesignPreviewDialog.PREVIEW_ICONS:
            row = CdwHBoxLayout(theme=self.theme)
            for selected in (False, True):
                button = CdwButton(DesignPreviewDialog.TEXT[key], variant=ButtonVariant.NAVIGATION, icon=icon, theme=self.theme)
                button.setChecked(selected)
                row.addWidget(button)
            content.addLayout(row)
        code = CdwSqlEditor(DesignPreviewDialog.TEXT["code"], compact=True, theme=self.theme)
        content.addWidget(code)
        table = CdwTableWidget(len(DesignPreviewDialog.PREVIEW_ROWS), len(DesignPreviewDialog.PREVIEW_HEADERS), theme=self.theme)
        table.setHorizontalHeaderLabels(DesignPreviewDialog.PREVIEW_HEADERS)
        for row, values in enumerate(DesignPreviewDialog.PREVIEW_ROWS):
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        content.addWidget(table)
        close = CdwButton(DesignPreviewDialog.TEXT["close"], icon=IconName.CLOSE, theme=self.theme)
        close.clicked.connect(self.accept)
        layout.addWidget(close)
