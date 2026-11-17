"""Qt snapshot motion inside ordinary widgets, independent of a compositor.

The style specification selects duration, distance and easing. This adapter only
captures, clips and presents those choices; logical Qt selection changes remain
immediate and are never replayed by an animation.
"""

from __future__ import annotations

from types import MappingProxyType

from qtpy.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QObject, QPoint, QPointF, QRect, Qt, QVariantAnimation
from qtpy.QtGui import QPainter, QPixmap
from qtpy.QtWidgets import QStackedWidget, QWidget

from cdw.ui.design.spec import MotionEasing, MotionRole


class QtMotion:
    """The single translation of design easing names to Qt implementation enums."""

    EASING_TYPES = MappingProxyType({MotionEasing.OUT_CUBIC: QEasingCurve.OutCubic})

    @classmethod
    def easing_curve(cls, easing: MotionEasing) -> QEasingCurve:
        return QEasingCurve(cls.EASING_TYPES[MotionEasing(easing)])


class _SnapshotOverlay(QWidget):
    """An opaque child surface; snapshots never become interactive controls."""

    OBJECT_NAME = "cdw_motion_overlay"
    POINTER_EVENTS = frozenset((
        QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick,
        QEvent.MouseMove, QEvent.Wheel, QEvent.ContextMenu,
        QEvent.TouchBegin, QEvent.TouchUpdate, QEvent.TouchEnd, QEvent.TouchCancel,
    ))
    START = 0.0
    END = 1.0

    def __init__(self, host: QWidget):
        super().__init__(host)
        self._outgoing = QPixmap()
        self._incoming = QPixmap()
        self._fraction = self.END
        self._distance = 0
        self._direction = 1
        self._background = host.palette().color(host.backgroundRole())
        self.setObjectName(self.OBJECT_NAME)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self.hide()

    def present(self, outgoing, incoming, rect, distance, direction, background):
        self._outgoing = outgoing
        self._incoming = incoming
        self._fraction = self.START
        self._distance = distance
        self._direction = direction
        self._background = background
        self.setGeometry(rect)
        self.show()
        self.raise_()
        self.update()

    def set_fraction(self, fraction):
        self._fraction = max(self.START, min(self.END, float(fraction)))
        self.update()

    def clear(self):
        self.hide()
        self._outgoing = QPixmap()
        self._incoming = QPixmap()
        self._fraction = self.END

    def snapshot(self) -> QPixmap:
        ratio = self.devicePixelRatioF()
        frame = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
        frame.setDevicePixelRatio(ratio)
        frame.fill(self._background)
        painter = QPainter(frame)
        try:
            self._paint(painter)
        finally:
            painter.end()
        return frame

    def _paint(self, painter):
        painter.setClipRect(self.rect())
        painter.fillRect(self.rect(), self._background)
        if self._incoming.isNull() or self._outgoing.isNull():
            return
        incoming_x = self._direction * self._distance * (self.END - self._fraction)
        outgoing_x = -self._direction * self._distance * self._fraction
        # Drawing the opaque destination first produces a true crossfade in the
        # overlapping area without allocating an alpha layer on every frame.
        painter.drawPixmap(QPointF(incoming_x, self.START), self._incoming)
        painter.setOpacity(self.END - self._fraction)
        painter.drawPixmap(QPointF(outgoing_x, self.START), self._outgoing)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def event(self, event):
        if event.type() in self.POINTER_EVENTS:
            event.accept()
            return True
        return super().event(event)


class ContentTransition(QObject):
    """Auto-bind a stacked widget, or animate explicit snapshots in any host.

    Construct once with ``ContentTransition(stack, theme, MotionRole.TAB)``.
    Native clicks, keyboard selection and programmatic changes share Qt's own
    ``currentChanged`` path. ``capture`` / ``play`` also support callers that own
    an explicit before/after mutation boundary.
    """

    # Bounds the cost of two software-rendered captures on remote desktop hosts.
    MAX_CAPTURE_PIXELS = 8_000_000
    START = 0.0
    END = 1.0
    FORWARD = 1
    BACKWARD = -1
    CANCEL_HOST_EVENTS = frozenset((QEvent.Resize, QEvent.Hide, QEvent.ParentChange))

    def __init__(self, host: QWidget, theme, role: MotionRole):
        super().__init__(host)
        self.host = host
        self.theme = theme
        self.role = MotionRole(role)
        self._overlay = _SnapshotOverlay(host)
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(self.START)
        self._animation.setEndValue(self.END)
        self._animation.valueChanged.connect(self._overlay.set_fraction)
        self._animation.finished.connect(self._finished)
        self._previous = None
        self._previous_index = -1
        self._capturing = False
        host.installEventFilter(self)
        theme.changed.connect(self._theme_changed)
        if isinstance(host, QStackedWidget):
            self._track(host.currentWidget())
            self._previous_index = host.currentIndex()
            host.currentChanged.connect(self._current_changed)
            host.widgetRemoved.connect(self._topology_changed)

    @property
    def running(self) -> bool:
        return self._animation.state() == QAbstractAnimation.Running

    @property
    def active(self) -> bool:
        """Snapshots remain active while a presentation capture pauses motion."""
        return self._animation.state() != QAbstractAnimation.Stopped

    def _can_capture(self, widget: QWidget, rect: QRect) -> bool:
        if not self.host.isVisible() or rect.isEmpty():
            return False
        ratio = widget.devicePixelRatioF()
        return round(rect.width() * ratio) * round(rect.height() * ratio) <= self.MAX_CAPTURE_PIXELS

    def _capture_widget(self, widget, rect=None) -> QPixmap:
        if widget is None or self._capturing:
            return QPixmap()
        try:
            area = QRect(widget.rect() if rect is None else rect)
            if not self._can_capture(widget, area):
                return QPixmap()
            self._capturing = True
            grabbed = widget.grab(area)
            if grabbed.isNull():
                return grabbed
            # Even an unpainted/transparent child produces an opaque image. The
            # display server never has to blend this widget with another window.
            opaque = QPixmap(grabbed.size())
            opaque.setDevicePixelRatio(grabbed.devicePixelRatioF())
            opaque.fill(self.host.palette().color(self.host.backgroundRole()))
            painter = QPainter(opaque)
            try:
                painter.drawPixmap(QPoint(), grabbed)
            finally:
                painter.end()
            return opaque
        except RuntimeError:
            # A tab may be removed and deleted in the same native signal turn.
            return QPixmap()
        finally:
            self._capturing = False

    def capture(self, widget: QWidget | None = None, rect: QRect | None = None) -> QPixmap:
        """Capture the displayed composite during a rapid reversal, else content."""
        if self.active:
            return self._overlay.snapshot()
        target = widget or self.host
        return self._capture_widget(target, rect)

    def transition(self, previous: QWidget | None, current: QWidget | None, *, direction=FORWARD):
        """Animate an already-committed selection without changing Qt selection."""
        if previous is None or current is None or previous is current:
            self.stop()
            return
        try:
            rect = QRect(current.mapTo(self.host, QPoint()), current.size())
            if not self._can_capture(current, current.rect()):
                self.stop()
                return
            # Rendering after the native hide sequence has completed includes
            # hidden descendants. Grabbing inside Hide sees partially hidden
            # children and can produce an empty scroll-area/table background.
            outgoing = self._overlay.snapshot() if self.active else self._capture_widget(previous)
            incoming = self._capture_widget(current)
        except RuntimeError:
            self.stop()
            return
        self.play(outgoing, incoming, rect=rect, direction=direction)

    def play(self, outgoing: QPixmap, incoming: QPixmap, *, rect: QRect | None = None, direction=FORWARD):
        """Present bounded snapshots. Logical content is already the destination."""
        area = QRect(self.host.contentsRect() if rect is None else rect)
        rule = self.theme.current.motion(self.role)
        if (outgoing is None or incoming is None or outgoing.isNull() or incoming.isNull()
                or rule.duration_ms <= 0 or not self._can_capture(self.host, area)
                or not self.host.rect().contains(area)
                or outgoing.deviceIndependentSize().toSize() != area.size()
                or incoming.deviceIndependentSize().toSize() != area.size()):
            self.stop()
            return
        self._animation.stop()
        self._animation.setDuration(rule.duration_ms)
        self._animation.setEasingCurve(QtMotion.easing_curve(rule.easing))
        self._overlay.present(outgoing, incoming, area, rule.distance,
                              self.BACKWARD if direction < 0 else self.FORWARD,
                              self.host.palette().color(self.host.backgroundRole()))
        self._animation.start()

    def stop(self):
        """Immediately reveal the latest selected live content and release images."""
        self._animation.stop()
        self._overlay.clear()

    def _finished(self):
        self._overlay.clear()

    def _theme_changed(self, *_):
        self.stop()

    def _track(self, widget):
        self._previous = widget

    def _current_changed(self, index):
        current = self.host.currentWidget()
        previous = self._previous
        direction = self.FORWARD if index >= self._previous_index else self.BACKWARD
        try:
            still_present = previous is not None and self.host.indexOf(previous) >= 0
        except RuntimeError:
            still_present = False
        if still_present:
            self.transition(previous, current, direction=direction)
        else:
            self.stop()
        self._track(current)
        self._previous_index = index

    def _topology_changed(self, *_):
        self.stop()
        self._track(self.host.currentWidget())
        self._previous_index = self.host.currentIndex()

    def eventFilter(self, watched, event):
        if watched is self.host and event.type() in self.CANCEL_HOST_EVENTS:
            self.stop()
        return super().eventFilter(watched, event)
