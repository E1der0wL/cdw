"""Apply the same window policy to app and Qt-generated floating surfaces."""

from qtpy.QtCore import QEvent, QObject, Qt
from qtpy.QtGui import QPainter, QPalette
from qtpy.QtWidgets import QComboBox, QFrame, QGraphicsDropShadowEffect, QWidget

POPUP_FILTER_ATTRIBUTE = "_cdw_popup_style_filter"
POPUP_WINDOW_TYPES = frozenset((Qt.Popup, Qt.ToolTip))
POPUP_WINDOW_HINTS = Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
POPUP_SURFACE_PROPERTY = "cdbSurface"
POPUP_SURFACE = "popup"
POPUP_EVENTS = frozenset((QEvent.Polish, QEvent.Paint))


class PopupStyleFilter(QObject):
    def eventFilter(self, watched, event):
        if (event.type() not in POPUP_EVENTS or not isinstance(watched, QWidget)
                or not watched.isWindow() or watched.windowType() not in POPUP_WINDOW_TYPES):
            return super().eventFilter(watched, event)
        # Polish runs before the first show. Changing window flags in Show can
        # hide the popup and break the opening click or keyboard focus.
        if event.type() == QEvent.Polish:
            watched.setProperty(POPUP_SURFACE_PROPERTY, POPUP_SURFACE)
            watched.setAttribute(Qt.WA_StyledBackground, True)
            watched.setWindowFlags(watched.windowFlags() | POPUP_WINDOW_HINTS)
            if isinstance(watched, QFrame):
                watched.setFrameShape(QFrame.NoFrame)
            if isinstance(watched.graphicsEffect(), QGraphicsDropShadowEffect):
                watched.setGraphicsEffect(None)
        elif (watched.windowType() == Qt.Popup and isinstance(watched, QFrame)
              and isinstance(watched.parentWidget(), QComboBox)):
            # Qt paints PE_PanelMenu here even with NoFrame. Replace only the
            # empty container background; its child view/scrollers still paint
            # and handle selection normally.
            painter = QPainter(watched)
            painter.fillRect(event.rect(), watched.palette().color(QPalette.Window))
            painter.end()
            return True
        return super().eventFilter(watched, event)


def install_popup_style(application):
    # Theme managers may be replaced; the application needs only one filter.
    if getattr(application, POPUP_FILTER_ATTRIBUTE, None) is None:
        policy = PopupStyleFilter(application)
        application.installEventFilter(policy)
        setattr(application, POPUP_FILTER_ATTRIBUTE, policy)
