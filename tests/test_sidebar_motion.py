"""Sidebar motion preserves navigation, user geometry and compact hit areas."""

from dataclasses import replace
import os
from pathlib import Path
import re
import time
from types import SimpleNamespace

import pytest
from qtpy.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from qtpy.QtGui import QFontDatabase, QHelpEvent
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication, QButtonGroup, QToolTip

from cdw.domain.appearance import AppearanceSettings, ThemeMode
from cdw.i18n import tr
from cdw.ui.design.icons import IconName
from cdw.ui.design.manager import ThemeManager
from cdw.ui.design.spec import LayoutRole, TextRole
from cdw.ui.widgets import (
    CdwHBoxLayout, CdwLabel, CdwNavigationButton, CdwSidebar, CdwSidebarSplitter,
    CdwStackedWidget, CdwTitleArea, CdwVBoxLayout, CdwWidget,
)


WAIT_SECONDS = 3
NAVIGATION = (("Home", IconName.HOME), ("SQL workspace", IconName.DATABASE),
              ("Settings", IconName.SETTINGS), ("Profile", IconName.PROFILE))


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    if os.name == "nt" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        for filename in ("malgun.ttf", "malgunbd.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
    return app


@pytest.fixture
def shell_factory(application):
    windows = []

    def create(preferences=None):
        theme = ThemeManager(preferences or AppearanceSettings())
        theme.activate()
        window = CdwWidget(surface="window", theme=theme)
        root_layout = CdwHBoxLayout(window, role=LayoutRole.FLUSH, theme=theme)
        sidebar = CdwSidebar(theme=theme)
        sidebar_layout = CdwVBoxLayout(sidebar.content_widget, role=LayoutRole.FLUSH, theme=theme)
        title = CdwTitleArea("CDW", "Command data workspace", title_role=TextRole.BRAND,
                             subtitle_role=TextRole.CAPTION, theme=theme)
        title.header.insertWidget(0, sidebar.toggle_button)
        sidebar_layout.addWidget(title)
        sidebar.register_title(title)
        navigation = CdwVBoxLayout(role=LayoutRole.SIDEBAR, theme=theme)
        sidebar.register_navigation_layout(navigation)
        sidebar_layout.addLayout(navigation, 1)
        group = QButtonGroup(window)
        pages = CdwStackedWidget(theme=theme)
        buttons = []
        for index, (label, icon) in enumerate(NAVIGATION):
            if index == 2:
                navigation.addStretch()
            page = CdwWidget(surface="window", theme=theme)
            page_layout = CdwVBoxLayout(page, role=LayoutRole.PAGE, theme=theme)
            page_layout.addWidget(CdwLabel(label, theme=theme))
            pages.addWidget(page)
            button = CdwNavigationButton(label, icon=icon, theme=theme)
            group.addButton(button)
            navigation.addWidget(button)
            sidebar.register_navigation(button)
            button.clicked.connect(lambda _checked=False, selected=index: pages.setCurrentIndex(selected))
            buttons.append(button)
        footer = CdwLabel("CDW", role=TextRole.CAPTION, theme=theme)
        navigation.addWidget(footer)
        sidebar.register_expanded_widget(footer)
        buttons[0].setChecked(True)
        sidebar.apply_style(theme.current)
        splitter = CdwSidebarSplitter(sidebar, pages, theme=theme)
        root_layout.addWidget(splitter)
        window.resize(1400, 1000)
        windows.append(window)
        window.show()
        window.activateWindow()
        application.processEvents()
        return SimpleNamespace(window=window, sidebar=sidebar, splitter=splitter, pages=pages,
                               title=title, footer=footer, buttons=buttons, theme=theme, group=group)

    yield create
    QToolTip.hideText()
    for window in windows:
        window.close()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application.processEvents()


def wait_settled(application, shell):
    deadline = time.monotonic() + WAIT_SECONDS
    while shell.splitter.motion_running and time.monotonic() < deadline:
        application.processEvents()
        QTest.qWait(5)
    application.processEvents()
    assert not shell.splitter.motion_running


def assert_compact_squares(shell):
    sidebar = shell.sidebar
    for button in (*shell.buttons, sidebar.toggle_button):
        assert button.width() == button.height()
        assert button.width() >= button.iconSize().width()
        within_sidebar = button.rect().translated(button.mapTo(sidebar, QPoint()))
        assert sidebar.rect().contains(within_sidebar)
        assert abs(within_sidebar.center().x() - sidebar.rect().center().x()) <= 1
    assert not sidebar.isWindow()
    assert not sidebar.testAttribute(Qt.WA_TranslucentBackground)


def drag_sidebar(application, shell):
    initial = shell.sidebar.width()
    handle = shell.splitter.handle(1)
    start = handle.rect().center()
    QTest.mousePress(handle, Qt.LeftButton, pos=start)
    QTest.mouseMove(handle, start + QPoint(90, 0), delay=30)
    QTest.mouseRelease(handle, Qt.LeftButton, pos=start + QPoint(90, 0))
    application.processEvents()
    width = shell.sidebar.width()
    assert width > initial
    return width


def navigation_positions(shell):
    rows = tuple((button.mapTo(shell.sidebar, QPoint()), button.height()) for button in shell.buttons)
    toggle = shell.sidebar.toggle_button
    return rows, QRect(toggle.mapTo(shell.sidebar, QPoint()), toggle.size())


def capture_moving_positions(application, shell):
    frames = []
    deadline = time.monotonic() + WAIT_SECONDS
    while shell.splitter.motion_running and time.monotonic() < deadline:
        application.processEvents()
        frames.append((shell.sidebar.width(), navigation_positions(shell)))
        QTest.qWait(5)
    application.processEvents()
    assert not shell.splitter.motion_running
    return frames


@pytest.mark.parametrize("font_size", [8.0, 20.0])
@pytest.mark.parametrize("ui_scale", [0.8, 1.5])
def test_collapsed_navigation_and_toggle_stay_square_at_supported_extremes(application, shell_factory, font_size, ui_scale):
    shell = shell_factory(AppearanceSettings(font_size=font_size, ui_scale=ui_scale))
    QTest.mouseClick(shell.sidebar.toggle_button, Qt.LeftButton)
    assert shell.sidebar.collapsed
    assert all(button.text() == "" for button in shell.buttons)
    assert shell.title.heading.isHidden() and shell.title.description.isHidden()
    assert shell.footer.isHidden()
    assert shell.sidebar.toggle_button.toolTip() == tr("sidebar.expand")
    wait_settled(application, shell)
    assert_compact_squares(shell)
    assert not shell.splitter.handle(1).isEnabled()
    QTest.mouseClick(shell.sidebar.toggle_button, Qt.LeftButton)
    assert not shell.sidebar.collapsed
    wait_settled(application, shell)
    assert [button.text() for button in shell.buttons] == [label for label, _ in NAVIGATION]
    assert shell.splitter.handle(1).isEnabled()


def test_rapid_toggle_reversal_keeps_latest_state_and_user_drag_width(application, shell_factory):
    shell = shell_factory()
    width = drag_sidebar(application, shell)
    changes = []
    shell.sidebar.collapsedChanged.connect(changes.append)
    QTest.mouseClick(shell.sidebar.toggle_button, Qt.LeftButton)
    assert shell.sidebar.collapsed and shell.splitter.motion_running
    QTest.qWait(25)
    QTest.mouseClick(shell.sidebar.toggle_button, Qt.LeftButton)
    assert not shell.sidebar.collapsed
    QTest.qWait(15)
    QTest.mouseClick(shell.sidebar.toggle_button, Qt.LeftButton)
    assert shell.sidebar.collapsed
    wait_settled(application, shell)
    assert shell.sidebar.width() < width
    assert_compact_squares(shell)
    QTest.mouseClick(shell.sidebar.toggle_button, Qt.LeftButton)
    wait_settled(application, shell)
    assert changes == [True, False, True, False]
    assert not shell.sidebar.collapsed
    assert abs(shell.sidebar.width() - width) <= 1


def test_square_controls_inherit_and_render_the_shared_radius(application, shell_factory):
    shell = shell_factory()
    QTest.mouseClick(shell.sidebar.toggle_button, Qt.LeftButton)
    wait_settled(application, shell)
    assert_compact_squares(shell)
    radius = shell.theme.current.metric("radius")
    assert radius > 0
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", application.styleSheet())
    base = next(body for selector, body in rules if selector.strip() == "QPushButton")
    assert re.search(rf"border-radius:\s*{radius}px\s*;", base)
    for selector, body in rules:
        if "QPushButton" in selector and ("cdwCompact" in selector or 'cdwVariant="navigation"' in selector):
            override = re.search(r"border-radius:\s*([^;]+)", body)
            assert override is None or override.group(1).strip() == f"{radius}px"
    for button in (*shell.buttons, shell.sidebar.toggle_button):
        assert not button.styleSheet()
        assert button.mask().isEmpty()
    selected = shell.buttons[0]
    assert selected.isChecked()
    rendered = selected.grab().toImage()
    assert rendered.pixelColor(1, 1) != rendered.pixelColor(rendered.width() // 2, 2)


@pytest.mark.parametrize(("font_size", "ui_scale"), [(10.0, 1.0), (20.0, 1.5)])
def test_menu_rows_and_toggle_do_not_move_during_collapse_or_expansion(application, shell_factory, font_size, ui_scale):
    shell = shell_factory(AppearanceSettings(font_size=font_size, ui_scale=ui_scale))
    expanded_width = shell.sidebar.width()
    expanded_positions = navigation_positions(shell)
    assert shell.title.heading.isVisible() and shell.footer.isVisible()
    QTest.mouseClick(shell.sidebar.toggle_button, Qt.LeftButton)
    assert shell.sidebar.collapsed and shell.splitter.motion_running
    application.processEvents()
    assert navigation_positions(shell) == expanded_positions
    collapsing = capture_moving_positions(application, shell)
    collapsed_width = shell.sidebar.width()
    assert collapsed_width < expanded_width
    assert any(collapsed_width < width < expanded_width for width, _ in collapsing)
    assert all(positions == expanded_positions for _, positions in collapsing)
    assert navigation_positions(shell) == expanded_positions
    assert_compact_squares(shell)
    assert shell.title.heading.isHidden() and shell.title.description.isHidden()
    assert shell.footer.isHidden()
    QTest.mouseClick(shell.sidebar.toggle_button, Qt.LeftButton)
    assert not shell.sidebar.collapsed and shell.splitter.motion_running
    application.processEvents()
    assert navigation_positions(shell) == expanded_positions
    expanding = capture_moving_positions(application, shell)
    assert any(collapsed_width < width < expanded_width for width, _ in expanding)
    assert all(positions == expanded_positions for _, positions in expanding)
    assert navigation_positions(shell) == expanded_positions
    assert shell.sidebar.width() == expanded_width
    assert shell.title.heading.isVisible() and shell.title.description.isVisible()
    assert shell.footer.isVisible()


def test_resize_and_theme_change_during_motion_restore_valid_final_geometry(application, shell_factory):
    shell = shell_factory()
    expanded_width = drag_sidebar(application, shell)
    QTest.mouseClick(shell.sidebar.toggle_button, Qt.LeftButton)
    assert shell.splitter.motion_running
    shell.window.resize(1550, 1050)
    shell.theme.set_preferences(replace(shell.theme.preferences, mode=ThemeMode.DARK, font_size=20.0, ui_scale=1.5))
    wait_settled(application, shell)
    assert shell.sidebar.collapsed
    assert_compact_squares(shell)
    QTest.mouseClick(shell.sidebar.toggle_button, Qt.LeftButton)
    shell.window.resize(1400, 1000)
    shell.theme.set_preferences(replace(shell.theme.preferences, mode=ThemeMode.LIGHT, font_size=8.0, ui_scale=0.8))
    wait_settled(application, shell)
    assert not shell.sidebar.collapsed
    assert abs(shell.sidebar.width() - round(expanded_width * 0.8)) <= 1
    assert shell.splitter.handle(1).isEnabled()
    assert all(button.isVisible() and button.text() for button in shell.buttons)


def test_hidden_sidebar_applies_state_without_deferred_animation(application, shell_factory):
    shell = shell_factory()
    width = drag_sidebar(application, shell)
    shell.window.hide()
    application.processEvents()
    shell.sidebar.set_collapsed(True)
    assert shell.sidebar.collapsed and not shell.splitter.motion_running
    assert all(not button.text() for button in shell.buttons)
    shell.sidebar.set_collapsed(False)
    assert not shell.sidebar.collapsed and not shell.splitter.motion_running
    assert all(button.text() for button in shell.buttons)
    shell.window.show()
    application.processEvents()
    assert not shell.splitter.motion_running
    assert abs(shell.sidebar.width() - width) <= 1


def test_collapsed_navigation_retains_tooltips_and_keyboard_activation(application, shell_factory):
    shell = shell_factory()
    QTest.mouseClick(shell.sidebar.toggle_button, Qt.LeftButton)
    wait_settled(application, shell)
    first, second = shell.buttons[:2]
    assert second.accessibleName() == NAVIGATION[1][0]
    center = second.rect().center()
    application.sendEvent(second, QHelpEvent(QEvent.ToolTip, center, second.mapToGlobal(center)))
    application.processEvents()
    assert QToolTip.text() == NAVIGATION[1][0]
    QToolTip.hideText()
    first.setFocus(Qt.TabFocusReason)
    # An exclusive Qt button group has one tab stop. Arrow keys move within it;
    # Tab leaves the group instead of visiting every unchecked menu button.
    QTest.keyClick(first, Qt.Key_Tab)
    application.processEvents()
    assert application.focusWidget() not in shell.buttons
    first.setFocus(Qt.TabFocusReason)
    QTest.keyClick(first, Qt.Key_Down)
    application.processEvents()
    assert second.hasFocus()
    QTest.keyClick(second, Qt.Key_Space)
    assert shell.pages.currentIndex() == 1 and second.isChecked()
    QTest.keyClick(second, Qt.Key_Up)
    application.processEvents()
    assert first.hasFocus()
    QTest.keyClick(first, Qt.Key_Space)
    assert shell.pages.currentIndex() == 0 and first.isChecked()
    QTest.mouseClick(shell.sidebar.toggle_button, Qt.LeftButton)
    wait_settled(application, shell)
    assert first.isChecked()
    assert [button.toolTip() for button in shell.buttons] == [""] * len(NAVIGATION)
    assert [button.text() for button in shell.buttons] == [label for label, _ in NAVIGATION]
