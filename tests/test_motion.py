"""Native selection and input contracts while common motion presents snapshots."""

import time
from dataclasses import replace

import pytest
from qtpy.QtCore import QCoreApplication, QEvent, Qt
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication, QStackedWidget, QWidget

from cdw.domain.appearance import AppearanceSettings, ThemeMode
from cdw.ui.design.manager import ThemeManager
from cdw.ui.widgets import CdwButton, CdwTabWidget, CdwVBoxLayout, CdwWidget


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def tabs(application):
    theme = ThemeManager(AppearanceSettings())
    theme.activate()
    tabs = CdwTabWidget(theme=theme)
    tabs.resize(560, 320)
    pages = []
    buttons = []
    for index in range(3):
        page = CdwWidget(theme=theme)
        layout = CdwVBoxLayout(page, theme=theme)
        button = CdwButton(f"Action {index}", theme=theme)
        layout.addWidget(button)
        layout.addStretch()
        tabs.addTab(page, f"Tab {index}")
        pages.append(page)
        buttons.append(button)
    tabs.show()
    application.processEvents()
    try:
        yield tabs, pages, buttons, theme
    finally:
        tabs.close()
        tabs.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        application.processEvents()


def finish_motion(application, transition):
    deadline = time.monotonic() + 3
    while transition.running and time.monotonic() < deadline:
        application.processEvents()
        QTest.qWait(5)
    application.processEvents()
    assert not transition.running


def overlay_for(tabs):
    return tabs.findChild(QWidget, "cdw_motion_overlay")


@pytest.mark.parametrize("selection", ["mouse", "keyboard", "code"])
def test_native_tab_selection_commits_once_before_motion_finishes(application, tabs, selection):
    widget, pages, _, _ = tabs
    selections = []
    widget.currentChanged.connect(selections.append)
    if selection == "mouse":
        QTest.mouseClick(widget.tabBar(), Qt.LeftButton, pos=widget.tabBar().tabRect(1).center())
    elif selection == "keyboard":
        widget.tabBar().setFocus()
        QTest.keyClick(widget.tabBar(), Qt.Key_Right)
    else:
        widget.setCurrentIndex(1)
    assert widget.currentWidget() is pages[1]
    assert selections == [1]
    assert widget.transition.running
    overlay = overlay_for(widget)
    assert overlay.isVisible()
    finish_motion(application, widget.transition)
    assert selections == [1]
    assert not overlay.isVisible()
    assert widget.currentWidget() is pages[1]


def test_snapshot_overlay_is_clipped_opaque_child_and_blocks_stale_coordinate_clicks(application, tabs):
    widget, pages, buttons, _ = tabs
    clicks = []
    buttons[0].clicked.connect(lambda: clicks.append(0))
    buttons[1].clicked.connect(lambda: clicks.append(1))
    widget.setCurrentIndex(1)
    overlay = overlay_for(widget)
    stack = widget.findChild(QStackedWidget, options=Qt.FindDirectChildrenOnly)
    assert widget.transition.running
    assert overlay.parentWidget() is stack and not overlay.isWindow()
    assert overlay.geometry() == pages[1].geometry()
    assert stack.rect().contains(overlay.geometry())
    assert not overlay.testAttribute(Qt.WA_TranslucentBackground)
    assert overlay.testAttribute(Qt.WA_OpaquePaintEvent)
    assert not overlay.testAttribute(Qt.WA_TransparentForMouseEvents)
    assert all(page.graphicsEffect() is None for page in pages)
    assert all(button.graphicsEffect() is None for button in buttons)
    point = buttons[1].mapTo(stack, buttons[1].rect().center())
    target = stack.childAt(point)
    assert target is overlay
    QTest.mouseClick(target, Qt.LeftButton, pos=target.mapFrom(stack, point))
    assert clicks == []
    finish_motion(application, widget.transition)
    QTest.mouseClick(buttons[1], Qt.LeftButton)
    assert clicks == [1]


def test_rapid_reverse_begins_at_the_current_frame_and_latest_selection_wins(application, tabs):
    widget, pages, _, _ = tabs
    widget.setCurrentIndex(1)
    QTest.qWait(40)
    before = widget.transition.capture()
    assert not before.isNull()
    widget.setCurrentIndex(0)
    after = widget.transition.capture()
    assert before.toImage() == after.toImage()
    assert after.devicePixelRatioF() == widget.devicePixelRatioF()
    assert after.deviceIndependentSize().toSize() == pages[0].size()
    widget.setCurrentIndex(2)
    widget.setCurrentIndex(1)
    assert widget.currentWidget() is pages[1]
    finish_motion(application, widget.transition)
    assert widget.currentWidget() is pages[1]
    assert not overlay_for(widget).isVisible()


def test_closing_selected_tab_during_motion_does_not_use_deleted_content(application, tabs):
    widget, pages, _, _ = tabs
    widget.setCurrentIndex(1)
    assert widget.transition.running
    widget.removeTab(1)
    pages[1].deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application.processEvents()
    assert widget.currentWidget() is pages[2]
    assert not widget.transition.running
    assert not overlay_for(widget).isVisible()
    widget.setCurrentIndex(0)
    assert widget.currentWidget() is pages[0]
    finish_motion(application, widget.transition)


def test_reordering_tabs_preserves_selected_widget_and_later_selection_still_animates(application, tabs):
    widget, pages, _, _ = tabs
    widget.setCurrentIndex(1)
    widget.tabBar().moveTab(1, 2)
    assert widget.currentWidget() is pages[1]
    finish_motion(application, widget.transition)
    widget.setCurrentIndex(0)
    assert widget.currentWidget() is pages[0]
    assert widget.transition.running
    finish_motion(application, widget.transition)


@pytest.mark.parametrize("change", ["resize", "hide", "theme"])
def test_geometry_visibility_and_theme_changes_cancel_to_latest_live_content(application, tabs, change):
    widget, pages, _, theme = tabs
    widget.setCurrentIndex(1)
    assert widget.transition.running
    if change == "resize":
        widget.resize(widget.width() + 40, widget.height() + 20)
    elif change == "hide":
        widget.hide()
    else:
        theme.set_preferences(replace(theme.preferences, mode=ThemeMode.DARK, ui_scale=1.2))
    application.processEvents()
    assert not widget.transition.running
    assert not overlay_for(widget).isVisible()
    assert widget.currentWidget() is pages[1]
    if change == "hide":
        widget.show()
        application.processEvents()
        assert not widget.transition.running


def test_hidden_tab_selection_has_no_initial_transition(application, tabs):
    widget, pages, _, _ = tabs
    widget.hide()
    widget.setCurrentIndex(2)
    assert widget.currentWidget() is pages[2]
    assert not widget.transition.running
    widget.show()
    application.processEvents()
    assert not widget.transition.running
