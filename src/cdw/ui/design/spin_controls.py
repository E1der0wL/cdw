"""One visual boundary for CDW and Qt-owned spin boxes.

Only paint events are replaced, with a content-derived minimum width. Qt retains the original editor, ranges, step
availability, signals and mouse/keyboard/wheel behavior, including QColorDialog's
private QSpinBox children. The adapter never reparents or replaces those widgets.
"""

from __future__ import annotations

import weakref

from qtpy.QtCore import QEvent, QObject, QRect, QTimer
from qtpy.QtWidgets import QAbstractSpinBox, QDoubleSpinBox, QSpinBox, QStyle, QStyleOptionSpinBox, QStylePainter

from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import VisualState


class SpinControlPainter:
    ICON_METRIC = "spin_icon"
    STEP_ICONS = ((QStyle.SC_SpinBoxUp, QAbstractSpinBox.StepUpEnabled, 180),
                  (QStyle.SC_SpinBoxDown, QAbstractSpinBox.StepDownEnabled, 0))

    @classmethod
    def paint(cls, spin, theme):
        option = QStyleOptionSpinBox()
        spin.initStyleOption(option)
        painter = QStylePainter(spin)
        try:
            painter.drawComplexControl(QStyle.CC_SpinBox, option)
            if option.buttonSymbols != QAbstractSpinBox.UpDownArrows:
                return
            style = theme.current
            for control, enabled, rotation in cls.STEP_ICONS:
                area = spin.style().subControlRect(QStyle.CC_SpinBox, option, control, spin)
                if not spin.isEnabled() or not option.stepEnabled & enabled:
                    state = VisualState.DISABLED
                elif option.activeSubControls & control:
                    state = VisualState.HOVER
                else:
                    state = VisualState.NORMAL
                size = min(style.metric(cls.ICON_METRIC), area.width(), area.height())
                rect = QRect(0, 0, size, size)
                rect.moveCenter(area.center())
                icon = theme.icons.icon(IconName.CHEVRON_DOWN, style.field_icon_color(state), rotation=rotation)
                icon.paint(painter, rect)
        finally:
            painter.end()


class SpinControlStyle(QObject):
    ADAPTER_ATTRIBUTE = "_cdw_spin_control_style"
    GEOMETRY_EVENTS = frozenset((QEvent.FontChange, QEvent.StyleChange, QEvent.PolishRequest,
                                 QEvent.Show, QEvent.Resize, QEvent.LayoutRequest))
    HORIZONTAL_SIDES = 2

    def __init__(self, spin, theme):
        super().__init__(spin)
        self._spin_ref = weakref.ref(spin)
        self.theme = theme
        self._original_minimum_width = spin.minimumWidth()
        self._applied_minimum_width = None
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.timeout.connect(self._ensure_minimum_width)
        spin.installEventFilter(self)
        theme.changed.connect(self.apply_style)
        self.apply_style(theme.current)

    @property
    def spin(self):
        reference = getattr(self, "_spin_ref", None)
        return reference() if reference is not None else None

    def apply_style(self, style):
        spin = self.spin
        if spin is not None:
            self._geometry_timer.start()
            spin.updateGeometry()
            spin.update()

    def _ensure_minimum_width(self):
        spin = self.spin
        if not isinstance(spin, (QSpinBox, QDoubleSpinBox)):
            return
        editor = spin.lineEdit()
        metrics = editor.fontMetrics()
        texts = [spin.prefix() + spin.textFromValue(value) + spin.suffix()
                 for value in (spin.minimum(), spin.maximum(), spin.value())]
        texts.append(spin.specialValueText())
        text_width = max(max(metrics.horizontalAdvance(text), metrics.boundingRect(text).width())
                         for text in texts)
        margins = editor.textMargins()
        native_style = spin.style()
        editor_inset = native_style.pixelMetric(QStyle.PM_FocusFrameHMargin, None, editor)
        cursor_width = native_style.pixelMetric(QStyle.PM_TextCursorWidth, None, editor)
        content_width = (text_width + margins.left() + margins.right()
                         + self.HORIZONTAL_SIDES * editor_inset + cursor_width)

        # A QSS spin box's size hint can reserve its integrated button twice while
        # still using the native editor's old width. Measure the actual styled
        # editor boundary instead of duplicating the stylesheet's padding rules.
        hint_width = max(spin.sizeHint().width(), spin.minimumSizeHint().width())
        option = QStyleOptionSpinBox()
        spin.initStyleOption(option)
        option.rect.setWidth(max(hint_width, spin.width()))
        editor_rect = native_style.subControlRect(QStyle.CC_SpinBox, option,
                                                 QStyle.SC_SpinBoxEditField, spin)
        non_editor_width = option.rect.width() - editor_rect.width()
        if spin.minimumWidth() != self._applied_minimum_width:
            self._original_minimum_width = spin.minimumWidth()
        minimum_width = min(spin.maximumWidth(), max(self._original_minimum_width, hint_width,
                                                     content_width + non_editor_width))
        self._applied_minimum_width = minimum_width
        if spin.minimumWidth() != minimum_width:
            spin.setMinimumWidth(minimum_width)

    def eventFilter(self, watched, event):
        spin = self.spin
        if spin is not None and watched is spin and event.type() == QEvent.Paint:
            SpinControlPainter.paint(spin, self.theme)
            return True
        if spin is not None and watched is spin and event.type() in self.GEOMETRY_EVENTS:
            self._geometry_timer.start()
        return False


def install_spin_control_style(spin, theme):
    """Attach the shared painter once without changing the spin box's behavior."""
    existing = getattr(spin, SpinControlStyle.ADAPTER_ATTRIBUTE, None)
    if existing is not None:
        return existing
    adapter = SpinControlStyle(spin, theme)
    setattr(spin, SpinControlStyle.ADAPTER_ATTRIBUTE, adapter)
    return adapter


def install_native_spin_controls(container, theme):
    """Style Qt-created child spin boxes while preserving their native ownership."""
    return tuple(install_spin_control_style(spin, theme) for spin in container.findChildren(QAbstractSpinBox))
