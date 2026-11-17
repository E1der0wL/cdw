"""One active application theme. Persistence is injected; Qt details stay here."""

from cdw.i18n import tr

import os
from typing import Callable

from qtpy.QtCore import QObject, Signal, QThread, Qt
from qtpy.QtGui import QFont, QFontDatabase, QPalette
from qtpy.QtWidgets import QApplication, QToolTip

from cdw.domain.appearance import AppearanceSettings
from cdw.domain.contracts import CdwError
from cdw.ui.design.icons import IconProvider
from cdw.ui.design.popups import install_popup_style
from cdw.ui.design.spec import CODE_TEXT_ROLES, ResolvedStyle, TextRole, resolve_style
from cdw.ui.design.stylesheet import build_stylesheet


class ThemeManager(QObject):

    APP_THEME_ATTRIBUTE = "_cdw_theme_manager"
    QT_STYLE = "Fusion"
    DISABLED_UI_EFFECTS = (Qt.UI_FadeTooltip, Qt.UI_AnimateTooltip, Qt.UI_AnimateCombo, Qt.UI_FadeMenu, Qt.UI_AnimateMenu)
    UI_FONT_CANDIDATES = ("Malgun Gothic",) if os.name == "nt" else ("Noto Sans CJK KR", "Noto Sans KR", "DejaVu Sans")
    CODE_FONT_CANDIDATES = ("Consolas",) if os.name == "nt" else ("DejaVu Sans Mono", "Liberation Mono")
    THREAD_ERROR = tr('theme.thread_error')
    PALETTE_ROLES = {
        QPalette.Window: "window", QPalette.WindowText: "text", QPalette.Base: "surface",
        QPalette.AlternateBase: "subtle", QPalette.Text: "text", QPalette.Button: "surface",
        QPalette.ButtonText: "text", QPalette.ToolTipBase: "tooltip_bg", QPalette.ToolTipText: "tooltip_text",
        QPalette.Highlight: "selection", QPalette.HighlightedText: "selection_text",
        QPalette.Link: "accent_text", QPalette.PlaceholderText: "muted", QPalette.BrightText: "on_accent",
        QPalette.Light: "surface", QPalette.Midlight: "subtle", QPalette.Mid: "border",
        QPalette.Dark: "border", QPalette.Shadow: "window", QPalette.Accent: "accent",
    }
    DISABLED_TEXT_ROLES = (QPalette.Text, QPalette.WindowText, QPalette.ButtonText, QPalette.PlaceholderText)

    changed = Signal(object)

    def __init__(self, preferences: AppearanceSettings | None = None,
                 save: Callable[[AppearanceSettings], None] | None = None):
        app = QApplication.instance()
        if app is None:
            raise RuntimeError(tr('ui.design.manager.thememanager.init'))
        super().__init__(app)
        self._save = save
        self.icons = IconProvider()
        self.current = resolve_style(preferences or AppearanceSettings())
        self.load_warning = ""
        self._fallback_font = app.font().family()
        self._available_fonts = frozenset(QFontDatabase.families())

    @property
    def preferences(self) -> AppearanceSettings:
        return self.current.preferences

    @classmethod
    def current_manager(cls) -> "ThemeManager":
        app = QApplication.instance()
        manager = getattr(app, ThemeManager.APP_THEME_ATTRIBUTE, None)
        if manager is None:
            manager = cls()
            manager.activate()
        return manager

    @classmethod
    def for_service(cls, service) -> "ThemeManager":
        warning = ""
        try:
            preferences = service.load_appearance()
        except CdwError as exc:
            preferences = AppearanceSettings()
            warning = exc.details
        manager = cls(preferences, service.save_appearance)
        manager.load_warning = warning
        manager.activate()
        return manager

    def activate(self):
        setattr(QApplication.instance(), ThemeManager.APP_THEME_ATTRIBUTE, self)
        install_popup_style(QApplication.instance())
        for effect in ThemeManager.DISABLED_UI_EFFECTS:
            QApplication.setEffectEnabled(effect, False)
        self._apply()

    def set_preferences(self, preferences: AppearanceSettings):
        if QThread.currentThread() != self.thread():
            raise RuntimeError(ThemeManager.THREAD_ERROR)
        resolved = resolve_style(preferences)
        # A rejected save must not change either the running UI or its source.
        if self._save is not None:
            self._save(preferences)
        self.current = resolved
        self.load_warning = ""
        self.icons.clear()
        self._apply()

    def font(self, role: TextRole = TextRole.BODY, style: ResolvedStyle | None = None) -> QFont:
        style = style or self.current
        code = role in CODE_TEXT_ROLES
        requested = style.preferences.code_font_family if code else style.preferences.font_family
        candidates = ThemeManager.CODE_FONT_CANDIDATES if code else ThemeManager.UI_FONT_CANDIDATES
        family = requested or next((name for name in candidates if name in self._available_fonts), self._fallback_font)
        font = QFont(family)
        font.setPointSizeF(style.font_points(role))
        font.setBold(style.spec.fonts[role].bold)
        return font

    def _apply(self):
        app = QApplication.instance()
        if QThread.currentThread() != self.thread():
            raise RuntimeError(ThemeManager.THREAD_ERROR)
        from qtpy.QtGui import QColor
        palette = QPalette()
        for role, token in ThemeManager.PALETTE_ROLES.items():
            palette.setColor(role, QColor(self.current.colors[token]))
        for role in ThemeManager.DISABLED_TEXT_ROLES:
            palette.setColor(QPalette.Disabled, role, QColor(self.current.colors["disabled_text"]))
        app.setStyle(ThemeManager.QT_STYLE) if app.style().objectName().lower() != ThemeManager.QT_STYLE.lower() else None
        app.setPalette(palette)
        app.setFont(self.font())
        QToolTip.setFont(self.font())
        app.setStyleSheet(build_stylesheet(self.current))
        self.changed.emit(self.current)
