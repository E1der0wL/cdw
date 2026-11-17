"""Opaque CDW window frames; native move/resize is isolated at this Qt boundary.

The controller reserves host contents margins and never replaces a main
window's central widget or a dialog's layout. Import this module lazily from
the common window/dialog constructors after their theme has been bound.
"""

from __future__ import annotations

import weakref

from qtpy.QtCore import QEvent, QObject, QPoint, QRect, Qt
from qtpy.QtGui import QColor, QPainter
from qtpy.QtWidgets import (
    QAbstractButton, QAbstractItemView, QAbstractSlider, QAbstractSpinBox,
    QApplication, QComboBox, QLineEdit, QPlainTextEdit, QSizePolicy, QTextEdit, QWidget,
)

from cdw.branding import APP_NAME
from cdw.i18n import tr
from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import ButtonVariant, LayoutRole
from cdw.ui.widgets import CdwHBoxLayout, CdwIconButton, CdwLabel, CdwWidget


class _FrameEdge(CdwWidget):
    """A solid edge child avoids painting over the host or using a window mask."""

    def __init__(self, parent, *, theme):
        super().__init__(parent, theme=theme)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self.theme.current.colors["border"]))


class WindowControl(CdwIconButton):
    """Window actions use common button states, icons and the chrome metrics."""

    def __init__(self, text, icon, parent, *, theme):
        super().__init__(parent=parent, icon=icon, variant=ButtonVariant.NAVIGATION, theme=theme)
        self.setCheckable(False)
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip(text)
        self.setAccessibleName(text)

    def apply_style(self, style):
        super().apply_style(style)
        self.setFixedSize(style.metric("window_control_width"),
                          style.metric("window_titlebar_height") - 2 * style.metric("border"))


class WindowTitleBar(CdwWidget):
    TEXT = {
        "minimize": tr("window.minimize"), "maximize": tr("window.maximize"),
        "restore": tr("window.restore"), "close": tr("window.close"),
    }

    def __init__(self, controller, *, theme):
        self._controller_ref = weakref.ref(controller)
        super().__init__(controller.host, theme=theme, surface="panel")
        self.setObjectName("cdw_window_titlebar")
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.row = CdwHBoxLayout(self, role=LayoutRole.WINDOW_TITLEBAR, theme=self.theme)
        self.application_icon = CdwLabel(parent=self, theme=self.theme)
        self.application_icon.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.application_icon.setAccessibleName(APP_NAME)
        self.title_label = CdwLabel(parent=self, theme=self.theme)
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.row.addWidget(self.application_icon)
        self.row.addWidget(self.title_label, 1)
        self.minimize_button = WindowControl(self.TEXT["minimize"], IconName.MINIMIZE, self, theme=self.theme)
        self.maximize_button = WindowControl(self.TEXT["maximize"], IconName.MAXIMIZE, self, theme=self.theme)
        self.close_button = WindowControl(self.TEXT["close"], IconName.CLOSE, self, theme=self.theme)
        self.minimize_button.setObjectName("window_minimize")
        self.maximize_button.setObjectName("window_maximize_restore")
        self.close_button.setObjectName("window_close")
        self.controls = (self.minimize_button, self.maximize_button, self.close_button)
        for button in self.controls:
            self.row.addWidget(button)
        self.minimize_button.clicked.connect(controller.host.showMinimized)
        self.maximize_button.clicked.connect(controller.toggle_maximized)
        self.close_button.clicked.connect(controller.host.close)
        self.minimize_button.setVisible(not controller.dialog)
        self.maximize_button.setVisible(not controller.dialog)
        self.apply_style(self.theme.current)
        self.refresh_window()

    def apply_style(self, style):
        super().apply_style(style)
        if not hasattr(self, "row"):
            return
        icon_size = style.metric("icon")
        self.application_icon.setFixedSize(icon_size, icon_size)
        self.refresh_window()
        self.update()

    def refresh_window(self):
        controller = self.controller
        host = controller.host if controller is not None else None
        if host is None:
            return
        title = host.windowTitle().replace("[*]", "*" if host.isWindowModified() else "")
        self.title_label.setText(title or APP_NAME)
        icon = host.windowIcon()
        if icon.isNull():
            icon = self.theme.icons.icon(IconName.TERMINAL, self.theme.current.colors["text"])
        size = self.theme.current.metric("icon")
        self.application_icon.setPixmap(icon.pixmap(size, size))
        maximized = host.isMaximized()
        button = self.maximize_button
        button.icon_name = IconName.RESTORE if maximized else IconName.MAXIMIZE
        label = self.TEXT["restore" if maximized else "maximize"]
        button.setToolTip(label)
        button.setAccessibleName(label)
        button._refresh_icon()

    @property
    def controller(self):
        reference = getattr(self, "_controller_ref", None)
        return reference() if reference is not None else None

    def drag_area(self, point):
        if not self.rect().contains(point):
            return False
        return not any(button.isVisible() and button.geometry().contains(point) for button in self.controls)

    def paintEvent(self, event):
        painter = QPainter(self)
        style = self.theme.current
        painter.fillRect(self.rect(), QColor(style.colors["sidebar"]))
        thickness = style.metric("border")
        painter.fillRect(0, self.height() - thickness, self.width(), thickness, QColor(style.colors["border"]))


class WindowChromeController(QObject):
    """One host's frame, hit testing and native/fallback drag lifecycle."""

    EMPTY_EDGES = Qt.Edge(0)
    WINDOW_HINTS = Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
    POINTER_OWNERS = (QAbstractButton, QAbstractItemView, QAbstractSlider, QAbstractSpinBox,
                      QComboBox, QLineEdit, QPlainTextEdit, QTextEdit)
    MOUSE_EVENTS = frozenset((QEvent.MouseButtonPress, QEvent.MouseButtonRelease,
                             QEvent.MouseMove, QEvent.MouseButtonDblClick))
    LAYOUT_EVENTS = frozenset((QEvent.Resize, QEvent.Show, QEvent.LayoutRequest, QEvent.WindowStateChange))
    TITLE_EVENTS = frozenset((QEvent.WindowTitleChange, QEvent.WindowIconChange, QEvent.WindowStateChange))
    CURSOR_BY_EDGES = {
        Qt.LeftEdge: Qt.SizeHorCursor, Qt.RightEdge: Qt.SizeHorCursor,
        Qt.TopEdge: Qt.SizeVerCursor, Qt.BottomEdge: Qt.SizeVerCursor,
        Qt.LeftEdge | Qt.TopEdge: Qt.SizeFDiagCursor,
        Qt.RightEdge | Qt.BottomEdge: Qt.SizeFDiagCursor,
        Qt.RightEdge | Qt.TopEdge: Qt.SizeBDiagCursor,
        Qt.LeftEdge | Qt.BottomEdge: Qt.SizeBDiagCursor,
    }

    def __init__(self, host, theme, *, dialog=False):
        super().__init__(host)
        self._host_ref = weakref.ref(host)
        self.theme = theme
        self.dialog = dialog
        self._layout_active = False
        self._drag_mode = None
        self._drag_edges = self.EMPTY_EDGES
        self._press_global = QPoint()
        self._initial_geometry = QRect()
        self._cursor_widget = None
        self._previous_cursor = None
        self._had_cursor = False
        # Apply the combined flags: QMainWindow's single-flag setter can discard
        # NoDropShadowWindowHint even though setWindowFlags preserves it.
        host.setWindowFlags(host.windowFlags() | self.WINDOW_HINTS)
        host.setAttribute(Qt.WA_TranslucentBackground, False)
        host.setAutoFillBackground(True)
        self.titlebar = WindowTitleBar(self, theme=theme)
        self.edges = tuple(_FrameEdge(host, theme=theme) for _ in range(4))
        self.theme.changed.connect(self.apply_style)
        QApplication.instance().installEventFilter(self)
        for widget in (host, *host.findChildren(QWidget)):
            if widget.window() is host:
                widget.setMouseTracking(True)
        self.apply_style(self.theme.current)

    @property
    def host(self):
        reference = getattr(self, "_host_ref", None)
        return reference() if reference is not None else None

    @property
    def _cursor_widget(self):
        reference = getattr(self, "_cursor_widget_ref", None)
        return reference() if reference is not None else None

    @_cursor_widget.setter
    def _cursor_widget(self, widget):
        # Hovering the host must not recreate a host -> controller -> host cycle.
        self._cursor_widget_ref = weakref.ref(widget) if widget is not None else None

    def apply_style(self, style):
        if self.host is None:
            return
        self._layout()
        self.titlebar.update()
        for edge in self.edges:
            edge.update()

    def _layout(self):
        if self.host is None or self._layout_active:
            return
        self._layout_active = True
        try:
            style = self.theme.current
            border = style.metric("border")
            header = style.metric("window_titlebar_height")
            margins = (border, border + header, border, border)
            current = self.host.contentsMargins()
            if (current.left(), current.top(), current.right(), current.bottom()) != margins:
                self.host.setContentsMargins(*margins)
            width, height = self.host.width(), self.host.height()
            self.titlebar.setGeometry(border, border, max(0, width - 2 * border), header)
            rectangles = (QRect(0, 0, width, border), QRect(0, max(0, height - border), width, border),
                          QRect(0, 0, border, height), QRect(max(0, width - border), 0, border, height))
            self.titlebar.raise_()
            for edge, rect in zip(self.edges, rectangles):
                edge.setGeometry(rect)
                edge.raise_()
        finally:
            self._layout_active = False

    def toggle_maximized(self):
        if self.dialog or self.host.isFullScreen():
            return
        if self.host.isMaximized():
            self.host.showNormal()
        else:
            self.host.showMaximized()

    def _resize_edges(self, point):
        if self.host.isMaximized() or self.host.isFullScreen() or not self.host.rect().contains(point):
            return self.EMPTY_EDGES
        margin = self.theme.current.metric("window_resize_margin")
        edges = self.EMPTY_EDGES
        if self.host.minimumWidth() < self.host.maximumWidth():
            if point.x() < margin:
                edges |= Qt.LeftEdge
            elif point.x() >= self.host.width() - margin:
                edges |= Qt.RightEdge
        if self.host.minimumHeight() < self.host.maximumHeight():
            if point.y() < margin:
                edges |= Qt.TopEdge
            elif point.y() >= self.host.height() - margin:
                edges |= Qt.BottomEdge
        return edges

    def _content_owns_pointer(self, widget, point):
        border = self.theme.current.metric("border")
        if not self.host.rect().adjusted(border, border, -border, -border).contains(point):
            return False
        current = widget
        while current is not None and current is not self.host:
            if current.property("cdwClickable") is True or isinstance(current, self.POINTER_OWNERS):
                return True
            current = current.parentWidget()
        return False

    def _reset_cursor(self):
        if self._cursor_widget is not None:
            try:
                if self._had_cursor:
                    self._cursor_widget.setCursor(self._previous_cursor)
                else:
                    self._cursor_widget.unsetCursor()
            except RuntimeError:
                pass
        self._cursor_widget = None

    def _set_edge_cursor(self, widget, edges):
        if widget is not self._cursor_widget:
            self._reset_cursor()
        if not edges:
            self._reset_cursor()
            return
        if self._cursor_widget is None:
            self._cursor_widget = widget
            self._previous_cursor = widget.cursor()
            self._had_cursor = widget.testAttribute(Qt.WA_SetCursor)
        widget.setCursor(self.CURSOR_BY_EDGES[edges])

    def _begin_drag(self, mode, global_position, edges=EMPTY_EDGES):
        handle = self.host.windowHandle()
        if handle is not None:
            try:
                accepted = handle.startSystemMove() if mode == "move" else handle.startSystemResize(edges)
                if accepted:
                    return
            except (AttributeError, RuntimeError):
                pass
        if mode == "move" and self.host.isMaximized():
            local = self.host.mapFromGlobal(global_position)
            proportion = local.x() / max(1, self.host.width())
            self.host.showNormal()
            self.host.move(global_position.x() - round(self.host.width() * proportion), global_position.y() - local.y())
        self._drag_mode = mode
        self._drag_edges = edges
        self._press_global = QPoint(global_position)
        self._initial_geometry = QRect(self.host.geometry())
        self.host.grabMouse()

    def _move_fallback(self, global_position):
        delta = global_position - self._press_global
        initial = self._initial_geometry
        if self._drag_mode == "move":
            self.host.move(initial.topLeft() + delta)
            return
        x, y, width, height = initial.x(), initial.y(), initial.width(), initial.height()
        if self._drag_edges & (Qt.LeftEdge | Qt.RightEdge):
            proposed = width - delta.x() if self._drag_edges & Qt.LeftEdge else width + delta.x()
            width = max(self.host.minimumWidth(), min(self.host.maximumWidth(), proposed))
            if self._drag_edges & Qt.LeftEdge:
                x = initial.right() - width + 1
        if self._drag_edges & (Qt.TopEdge | Qt.BottomEdge):
            proposed = height - delta.y() if self._drag_edges & Qt.TopEdge else height + delta.y()
            height = max(self.host.minimumHeight(), min(self.host.maximumHeight(), proposed))
            if self._drag_edges & Qt.TopEdge:
                y = initial.bottom() - height + 1
        self.host.setGeometry(x, y, width, height)

    def _end_drag(self):
        self._drag_mode = None
        self._drag_edges = self.EMPTY_EDGES
        if QWidget.mouseGrabber() is self.host:
            self.host.releaseMouse()

    def eventFilter(self, watched, event):
        # The host owns this QObject. Qt removes its application event filter
        # when the host destroys it; no Python destroyed-slot back-reference is
        # needed. A weak host also permits immediate unparented-dialog cleanup.
        host = self.host
        if host is None:
            return False
        kind = event.type()
        if kind in (QEvent.Polish, QEvent.Enter) and isinstance(watched, QWidget) and watched.window() is self.host:
            # New child widgets may be added after the common window constructor.
            # Tracking is local to this host; popup windows retain their own rules.
            watched.setMouseTracking(True)
        if watched is self.host:
            if kind in self.LAYOUT_EVENTS:
                self._layout()
            if kind in self.TITLE_EVENTS:
                self.titlebar.refresh_window()
            if kind in (QEvent.Hide, QEvent.Close, QEvent.WindowDeactivate):
                self._end_drag()
                self._reset_cursor()
        if kind == QEvent.Leave and watched is self._cursor_widget and self._drag_mode is None:
            self._reset_cursor()
        if kind not in self.MOUSE_EVENTS or not isinstance(watched, QWidget) or watched.window() is not self.host:
            return False
        global_position = event.globalPosition().toPoint()
        position = self.host.mapFromGlobal(global_position)
        edges = self._resize_edges(position)
        if edges and self._content_owns_pointer(watched, position):
            edges = self.EMPTY_EDGES
        if kind == QEvent.MouseMove:
            if self._drag_mode is not None:
                self._move_fallback(global_position)
                return True
            self._set_edge_cursor(watched, edges)
        elif kind == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton and self._drag_mode is not None:
            self._end_drag()
            return True
        elif event.button() == Qt.LeftButton:
            title_position = self.titlebar.mapFromGlobal(global_position)
            title_hit = self.titlebar.drag_area(title_position)
            if kind == QEvent.MouseButtonDblClick and title_hit and not edges:
                self.toggle_maximized()
                return True
            if kind == QEvent.MouseButtonPress:
                if edges:
                    self._begin_drag("resize", global_position, edges)
                    return True
                if title_hit:
                    self._begin_drag("move", global_position)
                    return True
        return False


def install_window_chrome(host, theme, *, dialog=False):
    """Install once on a common top-level widget and return its frame controller."""
    existing = getattr(host, "_cdw_window_chrome", None)
    if existing is not None:
        return existing
    controller = WindowChromeController(host, theme, dialog=dialog)
    host._cdw_window_chrome = controller
    return controller
