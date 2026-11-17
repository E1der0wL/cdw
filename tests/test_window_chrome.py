"""User-facing contracts for the opaque shared window frame and native fallback."""

from dataclasses import replace
import os
from pathlib import Path
import time
import weakref
from types import SimpleNamespace

import pytest
from qtpy.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, Qt
from qtpy.QtGui import QColor, QFontDatabase, QMouseEvent
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication

from cdw.domain.appearance import AppearanceSettings, ThemeMode
from cdw.i18n import tr
from cdw.ui.design.manager import ThemeManager
from cdw.ui.design.spec import LayoutRole
from cdw.ui.widgets import (
    CdwButton, CdwColorDialog, CdwComboBox, CdwDialog, CdwFileDialog, CdwLineEdit,
    CdwLogBar, CdwMainWindow, CdwMessageBox, CdwVBoxLayout, CdwWidget,
)


WAIT_SECONDS = 3


class CloseGuardWindow(CdwMainWindow):
    """A host can veto closing; window controls must respect its normal event."""

    def __init__(self, *, theme):
        self.close_attempts = 0
        self.allow_close = False
        super().__init__(theme=theme)

    def closeEvent(self, event):
        self.close_attempts += 1
        event.accept() if self.allow_close else event.ignore()


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    if os.name == "nt" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        for filename in ("malgun.ttf", "malgunbd.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
    return app


@pytest.fixture
def chrome_hosts(application):
    theme = ThemeManager(AppearanceSettings())
    theme.activate()
    hosts = []

    def own(host):
        hosts.append(host)
        return host

    yield theme, own
    for host in hosts:
        if isinstance(host, CloseGuardWindow):
            host.allow_close = True
        host.close()
        host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application.processEvents()


def wait_for(application, predicate):
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        QTest.qWait(5)
    raise AssertionError("The window did not reach the requested state")


def main_content(application, theme, own, *, guarded=False):
    host = own(CloseGuardWindow(theme=theme) if guarded else CdwMainWindow(theme=theme))
    content = CdwWidget(surface="window", theme=theme)
    layout = CdwVBoxLayout(content, role=LayoutRole.PAGE, theme=theme)
    editor = CdwLineEdit("Preserved content", theme=theme)
    action = CdwButton("Content action", theme=theme)
    layout.addWidget(editor)
    layout.addWidget(action)
    layout.addStretch()
    host.setCentralWidget(content)
    host.setMinimumSize(300, 240)
    host.setGeometry(100, 100, 640, 480)
    host.show()
    host.activateWindow()
    application.processEvents()
    return host, content, editor, action


def send_pointer(application, target, kind, global_position):
    button = Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton
    buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton
    event = QMouseEvent(kind, QPointF(target.mapFromGlobal(global_position)), QPointF(global_position),
                        button, buttons, Qt.NoModifier)
    application.sendEvent(target, event)


def unsupported_native_handle(monkeypatch, host):
    attempts = []
    native = SimpleNamespace(
        startSystemMove=lambda: attempts.append(("move", None)) or False,
        startSystemResize=lambda edges: attempts.append(("resize", edges)) or False,
    )
    # Only replace the platform's native capability result. All mouse hit tests
    # and fallback geometry still run through actual user events and Qt widgets.
    monkeypatch.setattr(host, "windowHandle", lambda: native)
    return attempts


@pytest.mark.parametrize("kind", ["main", "dialog", "message", "color", "file"])
def test_common_window_types_show_one_opaque_shared_frame(application, chrome_hosts, tmp_path, kind):
    theme, own = chrome_hosts
    factories = {
        "main": lambda: CdwMainWindow(theme=theme),
        "dialog": lambda: CdwDialog(theme=theme),
        "message": lambda: CdwMessageBox(theme=theme),
        "color": lambda: CdwColorDialog(QColor("#123456"), theme=theme),
        "file": lambda: CdwFileDialog(None, "Select file", str(tmp_path), theme=theme),
    }
    host = own(factories[kind]())
    if kind == "message":
        host.setText("A message remains inside the dialog content.")
        host.setStandardButtons(CdwMessageBox.Ok)
    host.setWindowTitle("Shared frame")
    host.show()
    application.processEvents()
    bar = host.window_chrome.titlebar
    assert host.windowFlags() & Qt.FramelessWindowHint
    assert host.windowFlags() & Qt.NoDropShadowWindowHint
    assert not host.testAttribute(Qt.WA_TranslucentBackground)
    assert host.windowOpacity() == 1.0 and host.mask().isEmpty()
    assert bar.parentWidget() is host and not bar.isWindow()
    assert len(host.findChildren(CdwWidget, "cdw_window_titlebar")) == 1
    assert bar.isVisible() and bar.title_label.text() == "Shared frame"
    assert bar.close_button.isVisible()
    assert bar.minimize_button.isVisible() == (kind == "main")
    assert bar.maximize_button.isVisible() == (kind == "main")
    if kind == "file":
        assert host.testOption(CdwFileDialog.DontUseNativeDialog)
    if kind == "color":
        assert host.testOption(CdwColorDialog.DontUseNativeDialog)


def test_title_and_theme_updates_preserve_main_content_and_dialog_layout(application, chrome_hosts):
    theme, own = chrome_hosts
    window, content, editor, _ = main_content(application, theme, own)
    dialog = own(CdwDialog(theme=theme))
    layout = CdwVBoxLayout(dialog, role=LayoutRole.DIALOG, theme=theme)
    field = CdwLineEdit("Dialog value", theme=theme)
    layout.addWidget(field)
    dialog.show()
    old_height = window.window_chrome.titlebar.height()
    window.setWindowTitle("Changed title")
    dialog.setWindowTitle("Changed dialog")
    theme.set_preferences(replace(theme.preferences, mode=ThemeMode.DARK, ui_scale=1.5))
    application.processEvents()
    assert window.centralWidget() is content and editor.text() == "Preserved content"
    assert dialog.layout() is layout and field.text() == "Dialog value"
    for host, expected in ((window, "Changed title"), (dialog, "Changed dialog")):
        bar = host.window_chrome.titlebar
        assert bar.title_label.text() == expected
        assert bar.height() > old_height
        assert host.contentsRect().top() > bar.geometry().bottom()
    assert content.mapTo(window, QPoint()).y() > window.window_chrome.titlebar.geometry().bottom()
    assert field.mapTo(dialog, QPoint()).y() > dialog.window_chrome.titlebar.geometry().bottom()
    bar = window.window_chrome.titlebar
    image = bar.grab().toImage()
    assert image.pixelColor(image.width() // 2, image.height() // 2).name() == theme.current.colors["sidebar"]


def test_window_controls_preserve_minimize_maximize_restore_and_close_veto(application, chrome_hosts):
    theme, own = chrome_hosts
    host, _, editor, _ = main_content(application, theme, own, guarded=True)
    bar = host.window_chrome.titlebar
    QTest.mouseClick(bar.maximize_button, Qt.LeftButton)
    wait_for(application, host.isMaximized)
    assert bar.maximize_button.toolTip() == tr("window.restore")
    QTest.mouseClick(bar.maximize_button, Qt.LeftButton)
    wait_for(application, lambda: not host.isMaximized())
    assert bar.maximize_button.toolTip() == tr("window.maximize")
    QTest.mouseClick(bar.minimize_button, Qt.LeftButton)
    wait_for(application, host.isMinimized)
    host.showNormal()
    wait_for(application, lambda: not host.isMinimized() and host.isVisible())
    assert editor.text() == "Preserved content"
    QTest.mouseClick(bar.close_button, Qt.LeftButton)
    assert host.close_attempts == 1 and host.isVisible()
    host.allow_close = True
    QTest.mouseClick(bar.close_button, Qt.LeftButton)
    assert host.close_attempts == 2 and not host.isVisible()


def test_enter_activates_dialog_content_without_triggering_window_close(application, chrome_hosts):
    theme, own = chrome_hosts
    dialog = own(CdwDialog(theme=theme))
    layout = CdwVBoxLayout(dialog, role=LayoutRole.DIALOG, theme=theme)
    editor = CdwLineEdit("Entered value", theme=theme)
    action = CdwButton("Apply value", theme=theme)
    layout.addWidget(editor)
    layout.addWidget(action)
    clicks = []
    action.clicked.connect(lambda: clicks.append(editor.text()))
    action.setAutoDefault(True)
    action.setDefault(True)
    dialog.show()
    dialog.activateWindow()
    editor.setFocus(Qt.TabFocusReason)
    application.processEvents()
    for control in dialog.window_chrome.titlebar.controls:
        assert not control.autoDefault() and not control.isDefault()
    QTest.keyClick(editor, Qt.Key_Return)
    application.processEvents()
    assert clicks == ["Entered value"] and dialog.isVisible()


def test_title_drag_falls_back_without_stealing_content_clicks(application, chrome_hosts, monkeypatch):
    theme, own = chrome_hosts
    host, _, _, action = main_content(application, theme, own)
    attempts = unsupported_native_handle(monkeypatch, host)
    bar = host.window_chrome.titlebar
    start = bar.mapToGlobal(QPoint(bar.width() // 2, bar.height() // 2))
    initial = QRect(host.geometry())
    delta = QPoint(45, 30)
    send_pointer(application, bar, QEvent.MouseButtonPress, start)
    send_pointer(application, host, QEvent.MouseMove, start + delta)
    send_pointer(application, host, QEvent.MouseButtonRelease, start + delta)
    application.processEvents()
    assert host.pos() == initial.topLeft() + delta
    assert attempts == [("move", None)]
    attempts.clear()
    clicks = []
    action.clicked.connect(lambda: clicks.append(True))
    before_click = QRect(host.geometry())
    QTest.mouseClick(action, Qt.LeftButton)
    application.processEvents()
    assert clicks == [True] and host.geometry() == before_click
    assert attempts == []


@pytest.mark.parametrize("leading", [True, False])
def test_edge_resize_fallback_respects_minimum_and_opposite_anchor(application, chrome_hosts, monkeypatch, leading):
    theme, own = chrome_hosts
    host, _, _, _ = main_content(application, theme, own)
    attempts = unsupported_native_handle(monkeypatch, host)
    initial = QRect(host.geometry())
    local = QPoint(1, 1) if leading else host.rect().bottomRight() - QPoint(1, 1)
    start = host.mapToGlobal(local)
    delta = QPoint(5000, 5000) if leading else QPoint(-5000, -5000)
    send_pointer(application, host, QEvent.MouseButtonPress, start)
    send_pointer(application, host, QEvent.MouseMove, start + delta)
    send_pointer(application, host, QEvent.MouseButtonRelease, start + delta)
    application.processEvents()
    assert host.size() == host.minimumSize()
    assert (host.geometry().bottomRight() if leading else host.geometry().topLeft()) == (
        initial.bottomRight() if leading else initial.topLeft())
    assert attempts and attempts[0][0] == "resize"
    settled = QRect(host.geometry())
    send_pointer(application, host, QEvent.MouseMove, start + delta + QPoint(50, 50))
    assert host.geometry() == settled


def test_unparented_dialogs_release_before_the_next_application_palette_update(application):
    def open_and_close():
        theme = ThemeManager(AppearanceSettings())
        theme.activate()
        dialog = CdwDialog(theme=theme)
        layout = CdwVBoxLayout(dialog, theme=theme)
        for _ in range(5):
            combo = CdwComboBox(theme=theme)
            combo.addItems(["First", "Second"])
            layout.addWidget(combo)
        dialog.show()
        application.processEvents()
        dialog.close()
        return weakref.ref(dialog)

    # Repeated app-wide palette updates previously encountered closed dialogs
    # retained by chrome back-references until Python GC destroyed their widgets.
    # No forced GC: removing the caller's last reference must release the host.
    for _ in range(40):
        reference = open_and_close()
        assert reference() is None
        application.processEvents()


def test_resize_hover_does_not_keep_an_unowned_window_alive(application):
    def hovered_dialog():
        theme = ThemeManager(AppearanceSettings())
        theme.activate()
        dialog = CdwDialog(theme=theme)
        dialog.show()
        application.processEvents()
        point = dialog.mapToGlobal(QPoint(1, dialog.height() // 2))
        event = QMouseEvent(QEvent.MouseMove, QPointF(dialog.mapFromGlobal(point)), QPointF(point),
                            Qt.NoButton, Qt.NoButton, Qt.NoModifier)
        application.sendEvent(dialog, event)
        assert dialog.cursor().shape() == Qt.SizeHorCursor
        return weakref.ref(dialog)

    reference = hovered_dialog()
    assert reference() is None


def test_log_bar_edge_clicks_belong_to_content_not_window_resize(application, chrome_hosts, monkeypatch):
    theme, own = chrome_hosts
    host, _, _, _ = main_content(application, theme, own)
    log_bar = CdwLogBar("Ready", host, theme=theme)
    host.setStatusBar(log_bar)
    application.processEvents()
    attempts = unsupported_native_handle(monkeypatch, host)
    activated = []
    log_bar.activated.connect(lambda: activated.append(True))
    geometry = QRect(host.geometry())
    for x in (1, log_bar.width() - 2):
        QTest.mouseClick(log_bar, Qt.LeftButton, pos=QPoint(x, log_bar.height() // 2))
    application.processEvents()
    assert activated == [True, True]
    assert attempts == [] and host.geometry() == geometry


def test_button_at_content_edge_receives_click_while_outer_border_resizes(application, chrome_hosts, monkeypatch):
    theme, own = chrome_hosts
    host = own(CdwMainWindow(theme=theme))
    button = CdwButton("Edge content", theme=theme)
    host.setCentralWidget(button)
    host.show()
    application.processEvents()
    attempts = unsupported_native_handle(monkeypatch, host)
    clicks = []
    button.clicked.connect(lambda: clicks.append(True))
    QTest.mouseClick(button, Qt.LeftButton, pos=QPoint(1, button.height() // 2))
    assert clicks == [True] and attempts == []
    edge = host.mapToGlobal(QPoint(0, host.height() // 2))
    send_pointer(application, host, QEvent.MouseButtonPress, edge)
    send_pointer(application, host, QEvent.MouseButtonRelease, edge)
    assert attempts and attempts[0][0] == "resize"
