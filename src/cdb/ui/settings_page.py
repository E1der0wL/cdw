from dataclasses import replace

from qtpy.QtGui import QFontDatabase
from qtpy.QtWidgets import QTableWidgetItem

from cdb.domain.appearance import AppearanceSettings, FONT_SIZE_RANGE, ThemeMode, UI_SCALE_RANGE
from cdb.domain.contracts import CdbError
from cdb.ui.design.icons import IconName
from cdb.ui.design.spec import ButtonVariant, LayoutRole, TextRole
from cdb.ui.widgets import (
    CdbButton, CdbColorEdit, CdbComboBox, CdbDialog, CdbDoubleSpinBox, CdbFormLayout,
    CdbHBoxLayout, CdbLabel, CdbLineEdit, CdbPage, CdbScrollArea, CdbSqlEditor,
    CdbSection, CdbSectionGroup, CdbTableWidget, CdbVBoxLayout,
)

MODE_LABELS = {ThemeMode.LIGHT: "라이트", ThemeMode.DARK: "다크"}
PERCENT = 100
SCALE_STEP = 5
FONT_STEP = 0.5
FONT_DECIMALS = 1
COLOR_FIELDS = (("accent", "accent"), ("background", "window"),
                ("secondary_background", "secondary_surface"), ("foreground", "text"))
TEXT = {
    "title": "설정", "subtitle": "색상과 글꼴, 화면 크기를 조정하세요. 변경사항은 바로 적용되고 자동으로 저장됩니다.",
    "appearance": "화면 설정",
    "mode": "화면 모드", "font": "화면 글꼴", "code_font": "SQL·명령어 글꼴",
    "accent": "강조 색", "background": "배경 색", "foreground": "전경 색",
    "secondary_background": "보조 배경 색",
    "color_help": "현재 화면 모드의 색상을 #RRGGBB로 입력하거나 선택하세요. 빈칸은 기본값입니다. 보조 배경 색은 탭·표 헤더, 표 교차 행, 다중 행 입력창과 카드에 적용됩니다.",
    "font_size": "기본 글자 크기", "scale": "UI 배율", "system_font": "기본 글꼴",
    "missing_font": "{name} · 이 장비에 없음", "font_help": "이 장비에 설치된 글꼴을 사용합니다. UI 배율은 글자·아이콘·화면 요소에 함께 적용됩니다.",
    "preview": "디자인 미리보기", "reset": "기본값으로 복원", "saved": "변경사항을 저장했습니다.",
    "failed": "설정을 저장하지 못했습니다. {details}", "environment": "현재 작업 환경",
    "preview_title": "디자인 미리보기", "preview_subtitle": "현재 설정으로 표시되는 주요 화면 요소입니다.",
    "buttons": "버튼과 상태", "primary": "실행", "secondary": "편집", "danger": "삭제", "disabled": "사용 불가",
    "typography": "데이터를 읽기 편한 화면", "muted": "설명과 보조 정보는 차분한 색상으로 표시됩니다.",
    "field": "입력 필드", "placeholder": "프리셋 이름을 입력하세요", "select": "선택 항목", "option": "샘플 데이터",
    "icons": "아이콘 · 기본 / 선택 상태", "home": "홈", "database": "데이터", "schedule": "일정",
    "close": "닫기", "code": "SELECT hostname, status\nFROM source.host_inventory;",
}
PREVIEW_HEADERS = ("hostname", "status", "cpu_percent")
PREVIEW_ROWS = (("rhel-app-01", "healthy", "23.5"), ("rhel-db-01", "warning", "67.2"))
PREVIEW_ICONS = (("home", IconName.HOME), ("database", IconName.DATABASE), ("schedule", IconName.SCHEDULE))


class SettingsPage(CdbPage):
    def __init__(self, environment: str, parent=None, *, theme=None):
        super().__init__(TEXT["title"], TEXT["subtitle"], parent, theme=theme)
        sections = CdbSectionGroup(theme=self.theme)
        card = CdbSection(TEXT["appearance"], theme=self.theme)
        form = CdbFormLayout(role=LayoutRole.ROW, theme=self.theme)
        card.content.addLayout(form)
        self.mode_combo = CdbComboBox(theme=self.theme)
        self.mode_combo.setObjectName("appearance_mode")
        for mode, title in MODE_LABELS.items():
            self.mode_combo.addItem(title, mode.value)
        self.color_edits = {}
        for name, _ in COLOR_FIELDS:
            control = CdbColorEdit(TEXT[name], theme=self.theme)
            control.setObjectName(f"appearance_{name}")
            self.color_edits[name] = control
        self.font_combo = self._font_combo("appearance_font")
        self.code_font_combo = self._font_combo("appearance_code_font")
        self.font_size_spin = CdbDoubleSpinBox(theme=self.theme)
        self.font_size_spin.setObjectName("appearance_font_size")
        self.font_size_spin.setRange(*FONT_SIZE_RANGE)
        self.font_size_spin.setDecimals(FONT_DECIMALS)
        self.font_size_spin.setSingleStep(FONT_STEP)
        self.font_size_spin.setSuffix(" pt")
        self.scale_spin = CdbDoubleSpinBox(theme=self.theme)
        self.scale_spin.setObjectName("appearance_scale")
        self.scale_spin.setRange(*(value * PERCENT for value in UI_SCALE_RANGE))
        self.scale_spin.setDecimals(0)
        self.scale_spin.setSingleStep(SCALE_STEP)
        self.scale_spin.setSuffix(" %")
        self._controls = (self.mode_combo, *self.color_edits.values(),
                          self.font_combo, self.code_font_combo, self.font_size_spin, self.scale_spin)
        for key, control in zip(("mode", *(name for name, _ in COLOR_FIELDS), "font", "code_font", "font_size", "scale"), self._controls):
            form.addRow(TEXT[key], control)
        color_help = CdbLabel(TEXT["color_help"], role=TextRole.MUTED, theme=self.theme)
        color_help.setWordWrap(True)
        form.addRow(color_help)
        help_text = CdbLabel(TEXT["font_help"], role=TextRole.MUTED, theme=self.theme)
        help_text.setWordWrap(True)
        form.addRow(help_text)
        sections.add_section(card)
        actions = CdbHBoxLayout(theme=self.theme)
        self.preview_button = CdbButton(TEXT["preview"], icon=IconName.TABLE, theme=self.theme)
        self.preview_button.clicked.connect(self._preview)
        self.reset_button = CdbButton(TEXT["reset"], icon=IconName.RESET, theme=self.theme)
        self.reset_button.setObjectName("appearance_reset")
        self.reset_button.clicked.connect(lambda: self._save(AppearanceSettings()))
        actions.addWidget(self.preview_button)
        actions.addWidget(self.reset_button)
        actions.addStretch()
        card.content.addLayout(actions)
        self.feedback = CdbLabel(self.theme.load_warning, role=TextRole.MUTED, theme=self.theme)
        self.feedback.setWordWrap(True)
        card.content.addWidget(self.feedback)
        info_card = CdbSection(TEXT["environment"], theme=self.theme)
        info = info_card.content
        environment_label = CdbLabel(environment, role=TextRole.MUTED, theme=self.theme)
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
        self.scale_spin.valueChanged.connect(lambda value: self._change(ui_scale=value / PERCENT))

    def _font_combo(self, name):
        combo = CdbComboBox(theme=self.theme)
        combo.setObjectName(name)
        combo.addItem(TEXT["system_font"], "")
        for family in sorted(QFontDatabase.families()):
            combo.addItem(family, family)
        return combo

    def _load(self, style):
        settings = style.preferences
        for control in self._controls:
            control.blockSignals(True)
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(settings.mode.value))
        for name, token in COLOR_FIELDS:
            self.color_edits[name].setText(getattr(settings.active_colors, name))
            self.color_edits[name].setPlaceholderText(style.colors[token])
        for combo, family in ((self.font_combo, settings.font_family), (self.code_font_combo, settings.code_font_family)):
            if combo.findData(family) < 0:
                combo.addItem(TEXT["missing_font"].format(name=family), family)
            combo.setCurrentIndex(combo.findData(family))
        self.font_size_spin.setValue(settings.font_size)
        self.scale_spin.setValue(settings.ui_scale * PERCENT)
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
        self.feedback.setText(TEXT["failed"].format(details=str(exc)))

    def _save(self, settings):
        try:
            self.theme.set_preferences(settings)
        except (CdbError, ValueError, OSError) as exc:
            self._show_save_error(exc)
            return
        self.feedback.setText(TEXT["saved"])

    def _preview(self):
        DesignPreviewDialog(self, theme=self.theme).exec()


class DesignPreviewDialog(CdbDialog):
    def __init__(self, parent=None, *, theme=None):
        super().__init__(parent, theme=theme)
        self.setWindowTitle(TEXT["preview_title"])
        self.resize(self.theme.current.metric("preview_width"), self.theme.current.metric("preview_height"))
        layout = CdbVBoxLayout(self, role=LayoutRole.DIALOG, theme=self.theme)
        scroll = CdbScrollArea(theme=self.theme)
        page = CdbPage(TEXT["preview_title"], TEXT["preview_subtitle"], theme=self.theme)
        scroll.setWidget(page)
        layout.addWidget(scroll)
        content = CdbVBoxLayout(role=LayoutRole.SECTION, theme=self.theme)
        page.content.addLayout(content)
        content.addWidget(CdbLabel(TEXT["typography"], theme=self.theme))
        content.addWidget(CdbLabel(TEXT["muted"], role=TextRole.MUTED, theme=self.theme))
        buttons = CdbHBoxLayout(theme=self.theme)
        for key, variant, icon in (("primary", ButtonVariant.PRIMARY, IconName.PLAY),
                                   ("secondary", ButtonVariant.SECONDARY, IconName.EDIT),
                                   ("danger", ButtonVariant.DANGER, IconName.DELETE),
                                   ("disabled", ButtonVariant.SECONDARY, IconName.STOP)):
            button = CdbButton(TEXT[key], variant=variant, icon=icon, theme=self.theme)
            button.setEnabled(key != "disabled")
            buttons.addWidget(button)
        content.addLayout(buttons)
        fields = CdbFormLayout(role=LayoutRole.ROW, theme=self.theme)
        entry = CdbLineEdit(theme=self.theme)
        entry.setPlaceholderText(TEXT["placeholder"])
        choice = CdbComboBox(theme=self.theme)
        choice.addItem(TEXT["option"])
        fields.addRow(TEXT["field"], entry)
        fields.addRow(TEXT["select"], choice)
        content.addLayout(fields)
        content.addWidget(CdbLabel(TEXT["icons"], role=TextRole.MUTED, theme=self.theme))
        for key, icon in PREVIEW_ICONS:
            row = CdbHBoxLayout(theme=self.theme)
            for selected in (False, True):
                button = CdbButton(TEXT[key], variant=ButtonVariant.NAVIGATION, icon=icon, theme=self.theme)
                button.setChecked(selected)
                row.addWidget(button)
            content.addLayout(row)
        code = CdbSqlEditor(TEXT["code"], compact=True, theme=self.theme)
        content.addWidget(code)
        table = CdbTableWidget(len(PREVIEW_ROWS), len(PREVIEW_HEADERS), theme=self.theme)
        table.setHorizontalHeaderLabels(PREVIEW_HEADERS)
        for row, values in enumerate(PREVIEW_ROWS):
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        content.addWidget(table)
        close = CdbButton(TEXT["close"], icon=IconName.CLOSE, theme=self.theme)
        close.clicked.connect(self.accept)
        layout.addWidget(close)
