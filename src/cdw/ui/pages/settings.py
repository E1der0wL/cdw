
from cdw.i18n import tr
from cdw.ui.design.preview import DesignPreviewDialog
from importlib.metadata import version
from cdw import __version__
from cdw.branding import APP_NAME
from cdw.config import BYTES_PER_MIB
from cdw.ui.pages.base import BasePage, PageId, PageSpec
from dataclasses import replace

from qtpy.QtGui import QFontDatabase

from cdw.domain.appearance import AppearanceSettings, FONT_SIZE_RANGE, ThemeMode, UI_SCALE_RANGE
from cdw.domain.contracts import CdwError
from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import LayoutRole, TextRole
from cdw.ui.widgets import CdwButton, CdwColorEdit, CdwComboBox, CdwDoubleSpinBox, CdwFormLayout, CdwHBoxLayout, CdwLabel, CdwSection, CdwSectionGroup


class SettingsPage(BasePage):

    MODE_LABELS = {ThemeMode.LIGHT: tr('appearance.light'), ThemeMode.DARK: tr('appearance.dark')}
    PERCENT = 100
    SCALE_STEP = 5
    FONT_STEP = 0.5
    FONT_DECIMALS = 1
    COLOR_FIELDS = (("accent", "accent"), ("background", "window"),
                    ("secondary_background", "secondary_surface"), ("foreground", "text"))
    TEXT = {
        'environment_details': tr('ui.pages.settings.text.environment_details'),
        'title': tr('pages.settings.title'),
        'subtitle': tr('ui.pages.settings.text.subtitle'),
        'appearance': tr('ui.pages.settings.text.appearance'),
        'mode': tr('ui.pages.settings.text.mode'),
        'font': tr('ui.pages.settings.text.font'),
        'code_font': tr('ui.pages.settings.text.code_font'),
        'accent': tr('ui.pages.settings.text.accent'),
        'background': tr('ui.pages.settings.text.background'),
        'foreground': tr('ui.pages.settings.text.foreground'),
        'secondary_background': tr('ui.pages.settings.text.secondary_background'),
        'color_help': tr('ui.pages.settings.text.color_help'),
        'font_size': tr('ui.pages.settings.text.font_size'),
        'scale': tr('ui.pages.settings.text.scale'),
        'system_font': tr('ui.pages.settings.text.system_font'),
        'missing_font': tr('ui.pages.settings.text.missing_font'),
        'font_help': tr('ui.pages.settings.text.font_help'),
        'preview': tr('ui.pages.settings.text.preview'),
        'reset': tr('ui.pages.settings.text.reset'),
        'saved': tr('ui.pages.settings.text.saved'),
        'failed': tr('ui.pages.settings.text.failed'),
        'environment': tr('ui.pages.settings.text.environment'),
        'buttons': tr('ui.pages.settings.text.buttons'),
    }

    SPEC = PageSpec(PageId.SETTINGS, TEXT["title"], TEXT["title"], TEXT["subtitle"], IconName.SETTINGS)
    def __init__(self, config, parent=None, *, theme=None):
        self.config = config
        super().__init__(parent, theme=theme)
        self.build_content()

    def build_content(self):
        config = self.config
        environment = SettingsPage.TEXT["environment_details"].format(directory=config.data_directory, timeout=config.execution_policy.timeout_seconds, mib=config.execution_policy.max_output_bytes // BYTES_PER_MIB, rows=config.query_policy.preview_rows, qt_version=version("PySide6"), app_name=APP_NAME, version=__version__)
        sections = CdwSectionGroup(theme=self.theme)
        card = CdwSection(SettingsPage.TEXT["appearance"], theme=self.theme)
        form = CdwFormLayout(role=LayoutRole.FORM, theme=self.theme)
        card.content.addLayout(form)
        self.mode_combo = CdwComboBox(theme=self.theme)
        self.mode_combo.setObjectName("appearance_mode")
        for mode, title in SettingsPage.MODE_LABELS.items():
            self.mode_combo.addItem(title, mode.value)
        self.color_edits = {}
        for name, _ in SettingsPage.COLOR_FIELDS:
            control = CdwColorEdit(SettingsPage.TEXT[name], theme=self.theme)
            control.setObjectName(f"appearance_{name}")
            self.color_edits[name] = control
        self.font_combo = self._font_combo("appearance_font")
        self.code_font_combo = self._font_combo("appearance_code_font")
        self.font_size_spin = CdwDoubleSpinBox(theme=self.theme)
        self.font_size_spin.setObjectName("appearance_font_size")
        self.font_size_spin.setRange(*FONT_SIZE_RANGE)
        self.font_size_spin.setDecimals(SettingsPage.FONT_DECIMALS)
        self.font_size_spin.setSingleStep(SettingsPage.FONT_STEP)
        self.font_size_spin.setSuffix(" pt")
        self.scale_spin = CdwDoubleSpinBox(theme=self.theme)
        self.scale_spin.setObjectName("appearance_scale")
        self.scale_spin.setRange(*(value * SettingsPage.PERCENT for value in UI_SCALE_RANGE))
        self.scale_spin.setDecimals(0)
        self.scale_spin.setSingleStep(SettingsPage.SCALE_STEP)
        self.scale_spin.setSuffix(" %")
        self._controls = (self.mode_combo, *self.color_edits.values(),
                          self.font_combo, self.code_font_combo, self.font_size_spin, self.scale_spin)
        for key, control in zip(("mode", *(name for name, _ in SettingsPage.COLOR_FIELDS), "font", "code_font", "font_size", "scale"), self._controls):
            form.addRow(SettingsPage.TEXT[key], control)
        color_help = CdwLabel(SettingsPage.TEXT["color_help"], role=TextRole.MUTED, theme=self.theme)
        color_help.setWordWrap(True)
        form.addRow(color_help)
        help_text = CdwLabel(SettingsPage.TEXT["font_help"], role=TextRole.MUTED, theme=self.theme)
        help_text.setWordWrap(True)
        form.addRow(help_text)
        sections.add_section(card)
        actions = CdwHBoxLayout(theme=self.theme)
        self.preview_button = CdwButton(SettingsPage.TEXT["preview"], icon=IconName.TABLE, theme=self.theme)
        self.preview_button.clicked.connect(self._preview)
        self.reset_button = CdwButton(SettingsPage.TEXT["reset"], icon=IconName.RESET, theme=self.theme)
        self.reset_button.setObjectName("appearance_reset")
        self.reset_button.clicked.connect(lambda: self._save(AppearanceSettings()))
        actions.addWidget(self.preview_button)
        actions.addWidget(self.reset_button)
        actions.addStretch()
        card.content.addLayout(actions)
        self.feedback = CdwLabel(self.theme.load_warning, role=TextRole.MUTED, theme=self.theme)
        self.feedback.setWordWrap(True)
        card.content.addWidget(self.feedback)
        info_card = CdwSection(SettingsPage.TEXT["environment"], theme=self.theme)
        info = info_card.content
        environment_label = CdwLabel(environment, role=TextRole.MUTED, theme=self.theme)
        environment_label.setWordWrap(True)
        info.addWidget(environment_label)
        sections.add_section(info_card)
        self.content.addWidget(sections)
        self.content.addStretch()
        self._load(self.theme.current)
        self.theme.changed.connect(self._load)
        self.mode_combo.currentIndexChanged.connect(lambda _: self._change(mode=ThemeMode(self.mode_combo.currentData())))
        for name, control in self.color_edits.items():
            control.colorCommitted.connect(lambda value, name=name: self._change_color(name, value))
        self.font_combo.currentIndexChanged.connect(lambda _: self._change(font_family=self.font_combo.currentData()))
        self.code_font_combo.currentIndexChanged.connect(lambda _: self._change(code_font_family=self.code_font_combo.currentData()))
        self.font_size_spin.valueChanged.connect(lambda value: self._change(font_size=value))
        self.scale_spin.valueChanged.connect(lambda value: self._change(ui_scale=value / SettingsPage.PERCENT))

    def _font_combo(self, name):
        combo = CdwComboBox(theme=self.theme)
        combo.setObjectName(name)
        combo.addItem(SettingsPage.TEXT["system_font"], "")
        for family in sorted(QFontDatabase.families()):
            combo.addItem(family, family)
        return combo

    def _load(self, style):
        settings = style.preferences
        for control in self._controls:
            control.blockSignals(True)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(settings.mode.value))
        for name, token in SettingsPage.COLOR_FIELDS:
            self.color_edits[name].setText(getattr(settings.active_colors, name))
            self.color_edits[name].setPlaceholderText(style.colors[token])
        for combo, family in ((self.font_combo, settings.font_family), (self.code_font_combo, settings.code_font_family)):
            if combo.findData(family) < 0:
                combo.addItem(SettingsPage.TEXT["missing_font"].format(name=family), family)
            combo.setCurrentIndex(combo.findData(family))
        self.font_size_spin.setValue(settings.font_size)
        self.scale_spin.setValue(settings.ui_scale * SettingsPage.PERCENT)
        for control in self._controls:
            control.blockSignals(False)

    def _change(self, **values):
        self._save(replace(self.theme.preferences, **values))

    def _change_color(self, name, value):
        try:
            settings = self.theme.preferences.with_active_colors(**{name: value})
        except ValueError as exc:
            self._show_save_error(exc)
            return
        if settings != self.theme.preferences:
            self._save(settings)

    def _show_save_error(self, exc):
        self._load(self.theme.current)
        self.feedback.setText(SettingsPage.TEXT["failed"].format(details=str(exc)))

    def _save(self, settings):
        try:
            self.theme.set_preferences(settings)
        except (CdwError, ValueError, OSError) as exc:
            self._show_save_error(exc)
            return
        self.feedback.setText(SettingsPage.TEXT["saved"])

    def _preview(self):
        DesignPreviewDialog(self, theme=self.theme).exec()
