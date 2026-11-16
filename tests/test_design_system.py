"""Public appearance contracts and real Qt settings interactions."""

import ast
import json
import os
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

import pytest
from qtpy.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from qtpy.QtGui import QColor, QFontDatabase, QFontMetrics, QHelpEvent, QPalette
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication, QDialogButtonBox, QLineEdit, QToolTip

from cdw.application.examples import demo_preset
from cdw.application.service import ApplicationService
from cdw.config import build_config
from cdw.domain.appearance import AppearanceSettings, ColorOverrides, ThemeMode
from cdw.domain.contracts import CdwError
from cdw.ui.design.icons import IconName, IconProvider, IconVariant, svg_source
from cdw.ui.design.manager import ThemeManager
from cdw.ui.design.spec import ButtonVariant, LayoutRole, MetricMode, MetricRule, TextRole, resolve_style
from cdw.ui.main_window import MainWindow, PAGE_SETTINGS, PAGE_SQL
from cdw.ui.preset_dialog import PresetDialog
from cdw.ui.settings_page import DesignPreviewDialog
from cdw.ui.widgets import CdwButton, CdwCard, CdwColorDialog, CdwComboBox, CdwLabel, CdwLineEdit, CdwVBoxLayout


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    if os.name == "nt" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        for filename in ("malgun.ttf", "malgunbd.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
    return app


def wait_idle(app, window):
    deadline = time.monotonic() + 15
    while window.controller.busy and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert not window.controller.busy
    app.processEvents()


@pytest.fixture
def window(application, tmp_path):
    window = MainWindow(ApplicationService(build_config(tmp_path)))
    window.show()
    wait_idle(application, window)
    yield window
    wait_idle(application, window)
    window.close()
    application.processEvents()


def capture(app, widget, name):
    if directory := os.environ.get("CDW_CAPTURE_DIR"):
        app.processEvents()
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        assert widget.grab().save(str(path / f"{name}.png"))


def test_settings_change_existing_and_new_widgets_and_survive_restart(application, window):
    window.show_page(PAGE_SETTINGS)
    page = window.settings_page
    # Open a real preset form before changing the global appearance.
    dialog = PresetDialog(window.service, parent=window)
    dialog.show()
    initial_icon = window.nav_buttons[PAGE_SQL].icon().cacheKey()
    initial_spacing = dialog.layout().spacing()
    page.mode_combo.setCurrentIndex(page.mode_combo.findData(ThemeMode.DARK.value))
    commit_color(application, page, "accent", "#c0a2f4")
    family = "Consolas" if "Consolas" in QFontDatabase.families() else QFontDatabase.families()[0]
    page.font_combo.setCurrentIndex(page.font_combo.findData(family))
    page.font_size_spin.setValue(12)
    page.scale_spin.setValue(125)
    application.processEvents()
    expected = AppearanceSettings(mode=ThemeMode.DARK, dark_colors=ColorOverrides(accent="#c0a2f4"),
                                  font_family=family, font_size=12, ui_scale=1.25)
    assert window.theme.preferences == expected
    assert window.service.load_appearance() == expected
    assert dialog.name_edit.font().pointSizeF() == 15
    assert window.sql_editor.font().pointSizeF() == 18
    assert window.sql_editor.document().defaultFont().pointSizeF() == 18
    assert dialog.name_edit.font().family() == family
    assert page.font_size_spin.lineEdit().font().pointSizeF() == 15
    assert page.scale_spin.lineEdit().font().pointSizeF() == 15
    assert dialog.tabs.tabBar().font().pointSizeF() == 15
    assert dialog.name_edit.minimumHeight() > QFontMetrics(dialog.name_edit.font()).height()
    assert dialog.layout().spacing() > initial_spacing
    assert window.nav_buttons[PAGE_SQL].icon().cacheKey() != initial_icon
    fresh = CdwLineEdit()
    assert fresh.font() == dialog.name_edit.font()
    fresh.deleteLater()
    dialog.close()
    capture(application, window, "settings-dark-violet-125")
    saved_config = window.service.config
    window.close()
    reopened = MainWindow(ApplicationService(saved_config))
    try:
        wait_idle(application, reopened)
        assert reopened.theme.preferences == expected
        assert reopened.settings_page.scale_spin.value() == 125
        assert reopened.settings_page.mode_combo.currentData() == ThemeMode.DARK.value
    finally:
        reopened.close()


def test_reset_and_hidden_internal_rules(application, window):
    window.show_page(PAGE_SETTINGS)
    page = window.settings_page
    page.mode_combo.setCurrentIndex(page.mode_combo.findData(ThemeMode.DARK.value))
    page.scale_spin.setValue(150)
    QTest.mouseClick(page.reset_button, Qt.LeftButton)
    application.processEvents()
    assert window.service.load_appearance() == AppearanceSettings()
    assert window.theme.preferences == AppearanceSettings()
    expected_names = {"appearance_mode", "appearance_font", "appearance_code_font",
                      "appearance_font_size", "appearance_scale", "appearance_accent", "appearance_background",
                      "appearance_secondary_background", "appearance_foreground"}
    assert {control.objectName() for control in page._controls} == expected_names
    assert page.findChild(CdwComboBox, "appearance_color") is None
    with sqlite3.connect(window.service.config.metadata_database) as connection:
        saved = json.loads(connection.execute("SELECT definition FROM app_settings WHERE key='appearance'").fetchone()[0])
    assert set(saved) == {"version", "mode", "font_family", "code_font_family", "font_size", "ui_scale", "light_colors", "dark_colors"}
    assert saved["light_colors"] == saved["dark_colors"] == {
        "accent": "", "background": "", "foreground": "", "secondary_background": ""}
    capture(application, window, "settings-light-teal")


def test_dropdown_arrow_opens_selectable_options(application, window):
    window.show_page(PAGE_SETTINGS)
    combo = window.settings_page.mode_combo
    application.processEvents()
    QTest.mouseClick(combo, Qt.LeftButton, pos=QPoint(combo.width() - 12, combo.height() // 2))
    application.processEvents()
    assert combo.view().isVisible()
    assert combo.view().window().mask().isEmpty()
    assert not combo.view().window().testAttribute(Qt.WA_TranslucentBackground)
    assert combo.view().window().windowFlags() & Qt.NoDropShadowWindowHint
    capture(application, combo.view().window(), "dropdown-options")
    # Qt suppresses release events briefly while opening its popup.
    QTest.qWait(application.doubleClickInterval())
    target = combo.model().index(combo.findData(ThemeMode.DARK.value), 0)
    QTest.mouseMove(combo.view().viewport(), combo.view().visualRect(target).center())
    QTest.mouseClick(combo.view().viewport(), Qt.LeftButton, pos=combo.view().visualRect(target).center())
    application.processEvents()
    assert window.theme.preferences.mode is ThemeMode.DARK
    assert not combo.view().isVisible()
    # Keyboard selection remains available with the custom arrow painting.
    combo.setFocus()
    QTest.keyClick(combo, Qt.Key_Up)
    assert window.theme.preferences.mode is ThemeMode.LIGHT
    window.theme.set_preferences(AppearanceSettings(mode=ThemeMode.DARK))
    QTest.mouseClick(combo, Qt.LeftButton, pos=QPoint(combo.width() - 12, combo.height() // 2))
    application.processEvents()
    assert combo.view().isVisible()
    capture(application, combo.view().window(), "dropdown-options-dark")
    QTest.keyClick(combo.view(), Qt.Key_Escape)
    assert not combo.view().isVisible()


def commit_color(application, page, name, value):
    edit = page.color_edits[name]
    edit.setFocus()
    edit.selectAll()
    QTest.keyClick(edit, Qt.Key_Backspace)
    QTest.keyClicks(edit, value)
    QTest.keyClick(edit, Qt.Key_Tab)
    application.processEvents()


def test_custom_colors_apply_globally_and_survive_mode_switch_and_restart(application, window):
    window.show_page(PAGE_SQL)
    window.sql_editor.setPlainText("SELECT 1 AS value")
    QTest.mouseClick(window.query_button, Qt.LeftButton)
    wait_idle(application, window)
    window.show_page(PAGE_SETTINGS)
    page = window.settings_page
    page.mode_combo.setCurrentIndex(page.mode_combo.findData(ThemeMode.DARK.value))
    dialog = PresetDialog(window.service, parent=window)
    original_icon = window.collect_button.icon().cacheKey()
    expected = ColorOverrides(accent="#ef946c", background="#30363b", foreground="#eeeeee", secondary_background="#454b50")
    for name, value in (("accent", "#EF946C"), ("background", "#30363B"),
                        ("secondary_background", "#454B50"), ("foreground", "#EEEEEE")):
        commit_color(application, page, name, value)
    assert window.theme.preferences.active_colors == expected
    for role in (QPalette.Window, QPalette.Base, QPalette.Button):
        assert application.palette().color(role).name() == expected.background
    assert window.sidebar.palette().color(QPalette.Window).name() == "#141719"
    assert window.log_bar.palette().color(QPalette.Window).name() == "#202020"
    assert dialog.name_edit.palette().color(QPalette.Base).name() == expected.background
    assert window.sql_editor.palette().color(QPalette.Base).name() == expected.secondary_background
    assert window.result_tabs.current_pane().view.palette().color(QPalette.AlternateBase).name() == expected.secondary_background
    assert window.result_tabs.current_pane().view.palette().color(QPalette.Text).name() == expected.foreground
    # Collection is disabled until a preset exists; disabled controls keep the base surface.
    assert not window.collect_button.isEnabled()
    assert window.collect_button.palette().color(QPalette.Button).name() == expected.background
    primary = CdwButton("실행", variant=ButtonVariant.PRIMARY)
    primary.ensurePolished()
    assert primary.palette().color(QPalette.Button).name() == expected.accent
    primary.deleteLater()
    assert window.collect_button.icon().cacheKey() != original_icon
    fresh = CdwLineEdit()
    assert fresh.palette().color(QPalette.Base).name() == expected.background
    fresh.deleteLater()
    dialog.close()
    capture(application, window, "settings-custom-colors")

    page.mode_combo.setCurrentIndex(page.mode_combo.findData(ThemeMode.LIGHT.value))
    assert window.theme.preferences.active_colors == ColorOverrides()
    commit_color(application, page, "background", "#fffdf5")
    page.mode_combo.setCurrentIndex(page.mode_combo.findData(ThemeMode.DARK.value))
    assert window.theme.preferences.active_colors == expected
    assert page.color_edits["background"].text() == expected.background
    saved = window.theme.preferences
    config = window.service.config
    window.close()
    reopened = MainWindow(ApplicationService(config))
    try:
        wait_idle(application, reopened)
        assert reopened.theme.preferences == saved
        assert saved.light_colors.background == "#fffdf5"
        assert reopened.theme.current.colors["surface"] == expected.background
        assert reopened.settings_page.color_edits["foreground"].text() == expected.foreground
        assert reopened.settings_page.color_edits["secondary_background"].text() == expected.secondary_background
    finally:
        reopened.close()


def test_color_validation_and_default_preserve_other_choices(application, window):
    window.show_page(PAGE_SETTINGS)
    page = window.settings_page
    commit_color(application, page, "background", "#fffdf5")
    before = window.theme.preferences
    commit_color(application, page, "background", "not-a-color")
    assert window.theme.preferences == window.service.load_appearance() == before
    assert page.color_edits["background"].text() == "#fffdf5"
    assert "#RRGGBB" in page.feedback.text()
    commit_color(application, page, "accent", "#804020")
    commit_color(application, page, "accent", "")
    assert window.theme.preferences.active_colors.accent == ""
    assert window.theme.preferences.active_colors.background == "#fffdf5"
    assert window.theme.current.colors["accent"] == "#137c6c"
    commit_color(application, page, "background", "")
    assert window.theme.current.colors["window"] == window.theme.current.colors["surface"] == "#f4f6fa"


def test_color_picker_accepts_and_cancel_preserves_saved_color(application, window):
    window.show_page(PAGE_SETTINGS)
    field = window.settings_page.color_edits["accent"]
    failures = []

    def choose(accept):
        picker = application.activeModalWidget()
        try:
            assert isinstance(picker, CdwColorDialog)
            picker.setCurrentColor(QColor("#f09151" if accept else "#713c99"))
            capture(application, picker, "color-picker")
            buttons = picker.findChild(QDialogButtonBox)
            QTest.mouseClick(buttons.button(QDialogButtonBox.Ok if accept else QDialogButtonBox.Cancel), Qt.LeftButton)
        except Exception as exc:
            failures.append(exc)
            if picker is not None:
                picker.reject()

    for accept in (True, False):
        QTimer.singleShot(50, lambda accept=accept: choose(accept))
        QTest.mouseClick(field.action_button, Qt.LeftButton)
        assert not failures, failures
        assert field.text() == "#f09151"
        assert window.service.load_appearance().active_colors.accent == "#f09151"


def test_spin_buttons_and_compact_log_bar_follow_font_settings(application, window):
    window.show_page(PAGE_SETTINGS)
    page = window.settings_page
    application.processEvents()
    for spin in (page.font_size_spin, page.scale_spin):
        before = spin.value()
        QTest.mouseClick(spin, Qt.LeftButton, pos=QPoint(spin.width() - 12, spin.height() // 4))
        application.processEvents()
        assert spin.value() == before + spin.singleStep()
        QTest.mouseClick(spin, Qt.LeftButton, pos=QPoint(spin.width() - 12, spin.height() * 3 // 4))
        application.processEvents()
        assert spin.value() == before
        spin.setValue(spin.minimum())
        QTest.mouseClick(spin, Qt.LeftButton, pos=QPoint(spin.width() - 12, spin.height() * 3 // 4))
        assert spin.value() == spin.minimum()
        spin.setValue(before)
    small = window.log_bar.height()
    assert QFontMetrics(window.log_bar.font()).height() <= small < page.font_size_spin.height()
    page.font_size_spin.setValue(20)
    page.scale_spin.setValue(150)
    application.processEvents()
    large = window.log_bar.height()
    assert large > small
    assert QFontMetrics(window.log_bar.font()).height() <= large <= QFontMetrics(window.log_bar.font()).height() + 8
    page.font_size_spin.setValue(10)
    page.scale_spin.setValue(100)
    application.processEvents()
    assert window.log_bar.height() == small
    capture(application, window, "cdw-settings")


def test_sidebar_collapses_keeps_navigation_and_shows_names_on_hover(application, window):
    assert window.windowTitle().startswith("CDW")
    width = window.sidebar.width()
    labels = {index: button.text() for index, button in window.nav_buttons.items()}
    QTest.mouseClick(window.sidebar.toggle_button, Qt.LeftButton)
    application.processEvents()
    assert window.sidebar.collapsed
    assert window.sidebar.width() < width
    for index, button in window.nav_buttons.items():
        assert button.text() == ""
        assert button.toolTip() == labels[index]
        assert not button.icon().isNull()
    button = window.nav_buttons[PAGE_SETTINGS]
    QTest.mouseClick(button, Qt.LeftButton)
    assert window.pages.currentIndex() == PAGE_SETTINGS
    center = button.rect().center()
    application.sendEvent(button, QHelpEvent(QEvent.ToolTip, center, button.mapToGlobal(center)))
    application.processEvents()
    assert QToolTip.text() == labels[PAGE_SETTINGS]
    for popup in QApplication.topLevelWidgets():
        if popup.isVisible() and popup.windowType() == Qt.ToolTip:
            assert popup.windowFlags() & Qt.NoDropShadowWindowHint
            capture(application, popup, "sidebar-tooltip")
    QToolTip.hideText()
    capture(application, window, "cdw-sidebar-collapsed")
    window.theme.set_preferences(AppearanceSettings(ui_scale=1.5))
    application.processEvents()
    assert window.sidebar.collapsed
    assert all(not button.text() for button in window.nav_buttons.values())
    QTest.mouseClick(window.sidebar.toggle_button, Qt.LeftButton)
    application.processEvents()
    assert not window.sidebar.collapsed
    assert {index: button.text() for index, button in window.nav_buttons.items()} == labels
    assert window.nav_buttons[PAGE_SETTINGS].isChecked()


def test_theme_change_during_collection_keeps_data_and_editor(application, window):
    preset = replace(demo_preset(window.service.config), arguments=("-c", "import time; time.sleep(0.3); print('[{\"value\":17}]')"))
    window.service.save_preset(preset)
    window.refresh_presets(preset.id)
    wait_idle(application, window)
    window.show_page(PAGE_SQL)
    sql = "SELECT value FROM source.host_inventory"
    window.sql_editor.setPlainText(sql)
    window.collect()
    window.theme.set_preferences(AppearanceSettings(mode=ThemeMode.DARK).with_active_colors(accent="#85b4ff"))
    wait_idle(application, window)
    assert window.sql_editor.toPlainText() == sql
    window.run_query()
    wait_idle(application, window)
    assert window.result_tabs.current_pane().current_result().rows == ((17,),)
    assert "1행 저장" in window.log_view.toPlainText()
    capture(application, window, "workspace-dark-blue")


def test_sidebar_drag_width_survives_collapse_and_font_scaling(application, window):
    window.show_page(PAGE_SETTINGS)
    application.processEvents()
    initial = window.sidebar.width()
    handle = window.shell_splitter.handle(1)
    start = handle.rect().center()
    QTest.mousePress(handle, Qt.LeftButton, pos=start)
    QTest.mouseMove(handle, start + QPoint(80, 0), delay=50)
    QTest.mouseRelease(handle, Qt.LeftButton, pos=start + QPoint(80, 0))
    application.processEvents()
    resized = window.sidebar.width()
    assert resized > initial
    assert window.sidebar.minimumWidth() <= resized <= window.sidebar.maximumWidth()
    QTest.mouseClick(window.sidebar.toggle_button, Qt.LeftButton)
    application.processEvents()
    assert window.sidebar.width() < initial
    QTest.mouseClick(window.sidebar.toggle_button, Qt.LeftButton)
    application.processEvents()
    assert abs(window.sidebar.width() - resized) <= 1
    window.resize(window.width() + 100, window.height())
    application.processEvents()
    assert abs(window.sidebar.width() - resized) <= 1
    window.theme.set_preferences(AppearanceSettings(ui_scale=1.25))
    application.processEvents()
    assert abs(window.sidebar.width() - round(resized * 1.25)) <= 1
    capture(application, window, "sidebar-resized")


@pytest.mark.parametrize("mode", list(ThemeMode))
@pytest.mark.parametrize("accent", ["", "#85b4ff"])
def test_modes_and_custom_accent_are_applied(application, mode, accent):
    manager = ThemeManager(AppearanceSettings(mode=mode).with_active_colors(accent=accent))
    manager.activate()
    panel = CdwCard()
    layout = CdwVBoxLayout(panel, role=LayoutRole.CARD)
    label = CdwLabel("한글 · readable", role=TextRole.BODY)
    button = CdwButton("실행", variant=ButtonVariant.PRIMARY, icon=IconName.PLAY)
    layout.addWidget(label)
    layout.addWidget(button)
    panel.show()
    application.processEvents()
    assert button.icon().isNull() is False
    assert button.iconSize() == QSize(18, 18)
    assert label.palette().color(label.foregroundRole()).name() == manager.current.colors["text"]
    assert panel.palette().color(panel.backgroundRole()).name() == manager.current.colors["secondary_surface"]
    panel.close()
    panel.deleteLater()


def test_widget_gallery_dark_and_scaled_layout_remain_accessible(application, window):
    window.theme.set_preferences(AppearanceSettings(mode=ThemeMode.DARK, ui_scale=1.25).with_active_colors(accent="#85b4ff"))
    preview = DesignPreviewDialog(window, theme=window.theme)
    preview.show()
    application.processEvents()
    capture(application, preview, "gallery-dark-blue")
    preview.close()
    window.theme.set_preferences(AppearanceSettings(mode=ThemeMode.DARK, font_size=20, ui_scale=1.5))
    window.show_page(PAGE_SETTINGS)
    application.processEvents()
    scroll = window.pages.currentWidget()
    assert scroll.verticalScrollBar().maximum() > 0
    for field in window.settings_page.findChildren(QLineEdit):
        # Spin-box internal editors are sized by Qt, other inputs by CDW.
        if isinstance(field, CdwLineEdit):
            assert field.height() >= QFontMetrics(field.font()).height()
    capture(application, window, "settings-large")


def test_icon_recoloring_variants_and_high_dpi(application):
    provider = IconProvider()
    for variant in IconVariant:
        for name in IconName:
            assert b"<svg" in svg_source(name, variant)
            icon = provider.icon(name, "#167cce", variant)
            pixmap = icon.pixmap(QSize(20, 20), 2.0)
            assert pixmap.size() == QSize(40, 40)
            assert pixmap.devicePixelRatio() == 2.0
            image = pixmap.toImage()
            painted = [image.pixelColor(x, y) for x in range(40) for y in range(40) if image.pixelColor(x, y).alpha() > 200]
            assert painted, (name, variant)
            assert all(abs(pixel.red() - 22) <= 1 and abs(pixel.green() - 124) <= 1 and abs(pixel.blue() - 206) <= 1 for pixel in painted)
    outline = provider.icon(IconName.HOME, "#167cce", IconVariant.OUTLINE).pixmap(24, 24).toImage()
    solid = provider.icon(IconName.HOME, "#167cce", IconVariant.SOLID).pixmap(24, 24).toImage()
    assert outline != solid


def test_internal_metric_modes_and_settings_validation():
    assert MetricRule(6).resolve(1.5) == 6
    assert MetricRule(20, MetricMode.SCALED).resolve(1.25) == 25
    bounded = MetricRule(8, MetricMode.CLAMPED, 6, 10)
    assert [bounded.resolve(scale) for scale in (0.5, 1.0, 2.0)] == [6, 8, 10]
    with pytest.raises(TypeError):
        resolve_style(AppearanceSettings()).metrics["padding_x"] = 100
    encoded = AppearanceSettings().to_dict()
    encoded["padding"] = 999
    with pytest.raises(ValueError):
        AppearanceSettings.from_dict(encoded)
    for value in (float("nan"), 0, 2):
        with pytest.raises(ValueError):
            AppearanceSettings(ui_scale=value)


def test_failed_save_preserves_running_theme(application):
    def reject(_):
        raise OSError("read-only profile")
    manager = ThemeManager(save=reject)
    manager.activate()
    before = manager.current
    with pytest.raises(OSError):
        manager.set_preferences(AppearanceSettings(mode=ThemeMode.DARK))
    assert manager.current is before


@pytest.mark.parametrize("version", [0, 1, 3])
def test_unsupported_metadata_is_rejected_without_migration(tmp_path, version):
    config = build_config(tmp_path)
    service = ApplicationService(config)
    preset = service.save_preset(demo_preset(config))
    service.close()
    with sqlite3.connect(config.metadata_database) as connection:
        connection.execute(f"PRAGMA user_version={version}")
    before = config.metadata_database.read_bytes()
    with pytest.raises(CdwError):
        ApplicationService(config)
    assert config.metadata_database.read_bytes() == before
    with sqlite3.connect(config.metadata_database) as connection:
        assert connection.execute("SELECT id FROM presets").fetchone()[0] == preset.id


def test_page_code_uses_cdw_widget_and_layout_subclasses():
    # Architectural boundary: Qt widgets are only constructed inside the common layer.
    directory = Path(__file__).parents[1] / "src" / "cdw" / "ui"
    forbidden = {"QWidget", "QPushButton", "QLabel", "QLineEdit", "QComboBox", "QVBoxLayout", "QHBoxLayout",
                 "QFormLayout", "QDialog", "QMainWindow", "QPlainTextEdit", "QTableView", "QTableWidget", "QFrame"}
    for filename in ("main_window.py", "preset_dialog.py", "settings_page.py", "log_dialog.py",
                     "result_tabs.py", "result_actions.py", "tasks_page.py", "task_dialog.py"):
        tree = ast.parse((directory / filename).read_text(encoding="utf-8"))
        calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        assert not calls & forbidden, filename
