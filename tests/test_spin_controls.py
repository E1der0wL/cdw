"""Actual color-picker input and layout contracts for shared spin controls."""

import os
from pathlib import Path
import time
import weakref

import pytest
from qtpy.QtCore import QCoreApplication, QEvent, Qt
from qtpy.QtGui import QColor, QFontDatabase
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication, QColorDialog, QSpinBox, QStyle, QStyleOptionSpinBox

from cdw.domain.appearance import AppearanceSettings, ThemeMode
from cdw.ui.design.manager import ThemeManager
from cdw.ui.design.spin_controls import install_native_spin_controls
from cdw.ui.widgets import CdwColorDialog


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    if os.name == "nt" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        for filename in ("malgun.ttf", "malgunbd.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
    return app


@pytest.fixture
def color_dialog(application):
    theme = ThemeManager(AppearanceSettings())
    theme.activate()
    dialog = CdwColorDialog(QColor(107, 211, 188), theme=theme)
    dialog.show()
    settle(application)
    yield dialog, theme
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application.processEvents()


def settle(application):
    deadline = time.monotonic() + 0.06
    while time.monotonic() < deadline:
        application.processEvents()
        QTest.qWait(5)


def click_step(spin, control):
    option = QStyleOptionSpinBox()
    spin.initStyleOption(option)
    rectangle = spin.style().subControlRect(QStyle.CC_SpinBox, option, control, spin)
    assert not rectangle.isEmpty()
    QTest.mouseClick(spin, Qt.LeftButton, pos=rectangle.center())


def enter_value(spin, text):
    spin.setFocus()
    spin.selectAll()
    QTest.keyClicks(spin, text)
    QTest.keyClick(spin, Qt.Key_Tab)


def test_color_channel_buttons_keyboard_and_bounds_update_current_color(application, color_dialog):
    dialog, _ = color_dialog
    # An initial red value distinct from all HSV/RGB values identifies the real
    # Qt editor independently of private object names or translated labels.
    red = next(spin for spin in dialog.findChildren(QSpinBox)
               if spin.value() == 107 and spin.maximum() == 255)
    emitted = []
    dialog.currentColorChanged.connect(lambda color: emitted.append(color.getRgb()))
    click_step(red, QStyle.SC_SpinBoxUp)
    assert red.value() == 108 and dialog.currentColor().getRgb() == (108, 211, 188, 255)
    click_step(red, QStyle.SC_SpinBoxDown)
    assert red.value() == 107 and dialog.currentColor().red() == 107

    enter_value(red, "200")
    assert dialog.currentColor().red() == 200
    red.setFocus()
    QTest.keyClick(red, Qt.Key_Up)
    assert red.value() == 201 and dialog.currentColor().red() == 201
    QTest.keyClick(red, Qt.Key_Down)
    assert dialog.currentColor().red() == 200

    enter_value(red, "255")
    click_step(red, QStyle.SC_SpinBoxUp)
    assert red.value() == 255 and dialog.currentColor().red() == 255
    enter_value(red, "0")
    click_step(red, QStyle.SC_SpinBoxDown)
    assert red.value() == 0 and dialog.currentColor().red() == 0
    assert emitted[0] == (108, 211, 188, 255) and emitted[-1] == (0, 211, 188, 255)
    assert dialog.isVisible()


@pytest.mark.parametrize("mode,font_size,scale", [
    (ThemeMode.LIGHT, 8, 0.8),
    (ThemeMode.DARK, 20, 1.5),
    (ThemeMode.LIGHT, 20, 1.5),
    (ThemeMode.DARK, 10, 0.8),
])
def test_theme_and_font_changes_keep_entire_channel_values_visible(
        application, color_dialog, mode, font_size, scale):
    dialog, theme = color_dialog
    original_spins = dialog.findChildren(QSpinBox)
    original_editors = [spin.lineEdit() for spin in original_spins]
    theme.set_preferences(AppearanceSettings(mode=mode, font_size=font_size, ui_scale=scale))
    settle(application)
    dialog.adjustSize()
    settle(application)
    assert dialog.findChildren(QSpinBox) == original_spins
    assert [spin.lineEdit() for spin in original_spins] == original_editors
    for spin in original_spins:
        if not spin.isVisible():
            continue
        editor = spin.lineEdit()
        margins = editor.textMargins()
        available = editor.contentsRect().width() - margins.left() - margins.right()
        assert editor.fontMetrics().horizontalAdvance(editor.text()) < available
        assert editor.font().pointSizeF() == pytest.approx(theme.font().pointSizeF())
    assert dialog.currentColor().getRgb() == (107, 211, 188, 255)
    assert not dialog.grab().isNull()


def test_install_preserves_native_editors_ownership_and_color_signals(application):
    theme = ThemeManager(AppearanceSettings())
    theme.activate()
    dialog = QColorDialog(QColor(107, 211, 188))
    dialog.setOption(QColorDialog.DontUseNativeDialog, True)
    try:
        spins = dialog.findChildren(QSpinBox)
        originals = [(spin, spin.parentWidget(), spin.lineEdit()) for spin in spins]
        first_adapters = install_native_spin_controls(dialog, theme)
        assert install_native_spin_controls(dialog, theme) == first_adapters
        assert dialog.findChildren(QSpinBox) == spins
        for spin, parent, editor in originals:
            assert spin.parentWidget() is parent and spin.lineEdit() is editor
        dialog.show()
        settle(application)
        blue = next(spin for spin in spins if spin.value() == 188)
        enter_value(blue, "75")
        assert dialog.currentColor().getRgb() == (107, 211, 75, 255)
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        application.processEvents()


def test_closed_color_dialog_and_native_spin_adapters_release_without_gc(application):
    theme = ThemeManager(AppearanceSettings())
    theme.activate()

    def open_and_close():
        dialog = CdwColorDialog(QColor(107, 211, 188), theme=theme)
        dialog.show()
        settle(application)
        spin = dialog.findChildren(QSpinBox)[0]
        adapter = install_native_spin_controls(dialog, theme)[0]
        references = (weakref.ref(dialog), weakref.ref(spin), weakref.ref(adapter))
        dialog.close()
        return references

    for _ in range(8):
        references = open_and_close()
        assert all(reference() is None for reference in references)
        theme.activate()
        application.processEvents()
