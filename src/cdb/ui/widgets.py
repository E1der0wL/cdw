"""CDW's Qt subclasses. New app widgets/layouts inherit their global rules here.

Pages choose semantic roles, not colors, padding or radii. Qt-generated internal
children (scrollbars, popup views, etc.) receive the same application QSS/palette.
"""

from qtpy.QtCore import QEvent, QRect, QRectF, QSize, Qt, Signal
from qtpy.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QRegion, QTransform
from qtpy.QtWidgets import (
    QAbstractItemView, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter, QSplitterHandle,
    QStackedWidget, QStatusBar, QTableView, QTableWidget, QTabWidget, QTreeWidget,
    QVBoxLayout, QWidget, QStyle, QStyledItemDelegate, QStyleOptionComboBox, QStyleOptionSpinBox,
)

from cdb.ui.design.icons import IconName, IconVariant
from cdb.ui.design.manager import ThemeManager
from cdb.ui.design.spec import ButtonVariant, LayoutRole, TextRole, VisualState

APPLICATION_EYEBROW = "LOCAL WORKSPACE"
BUTTON_TEXT = {QMessageBox.Yes: "예", QMessageBox.No: "아니요", QMessageBox.Ok: "확인", QMessageBox.Cancel: "취소"}
ICON_EVENTS = frozenset((QEvent.Enter, QEvent.Leave, QEvent.EnabledChange, QEvent.FocusIn, QEvent.FocusOut))
LABEL_COMPATIBILITY_ROLES = {"brandCaption": TextRole.CAPTION, **{role.value: role for role in TextRole}}
TABLE_MINIMUM_ROWS = 4
EDITOR_MINIMUM_LINES = 4
COMBO_MINIMUM_CHARACTERS = 12
COMBO_ICON_ROLE = Qt.UserRole + 1
SIDEBAR_TEXT = {True: "사이드바 펼치기", False: "사이드바 접기"}
SIDEBAR_ICON_ROTATIONS = {True: -90, False: 90}
CONTENT_HORIZONTAL_ALIGNMENT = Qt.AlignLeft
CONTENT_ALIGNMENT = CONTENT_HORIZONTAL_ALIGNMENT | Qt.AlignVCenter
COLOR_PICKER_ACTION = "선택"
TAB_BAR_GEOMETRY_EVENTS = frozenset((QEvent.Paint, QEvent.Resize, QEvent.Move,
                                    QEvent.LayoutRequest, QEvent.Show, QEvent.Hide))


class StyledWidgetMixin:
    TEXT_ROLE = TextRole.BODY
    CONTROL = False

    def _bind_theme(self, theme=None):
        self.theme = theme or ThemeManager.current_manager()
        self.setProperty("cdbManaged", True)
        self.theme.changed.connect(self.apply_style)
        self.apply_style(self.theme.current)

    def apply_style(self, style):
        self.setFont(self.theme.font(self.TEXT_ROLE, style))
        if self.CONTROL:
            content = QFontMetrics(self.font()).height()
            self.setMinimumHeight(max(style.metric("control_height"), content + 2 * style.metric("padding_y") + 2 * style.metric("border")))
        self.updateGeometry()


def _paint_field_icon(painter, widget, area, state, *, metric="icon", rotation=0):
    style = widget.theme.current
    size = min(style.metric(metric), area.width(), area.height())
    rect = QRect(0, 0, size, size)
    rect.moveCenter(area.center())
    widget.theme.icons.icon(IconName.CHEVRON_DOWN, style.field_icon_color(state), rotation=rotation).paint(painter, rect)


def _paint_divider(painter, rect, orientation, style, *, padding_metric="section_padding", active=False):
    thickness = style.metric("border")
    padding = style.metric(padding_metric)
    if orientation == Qt.Horizontal:
        line = QRect(rect.left() + padding, rect.center().y(), max(0, rect.width() - 2 * padding), thickness)
    else:
        line = QRect(rect.center().x(), rect.top() + padding, thickness, max(0, rect.height() - 2 * padding))
    painter.fillRect(line, style.colors["accent" if active else "border"])


class CdbWidget(StyledWidgetMixin, QWidget):
    def __init__(self, *args, theme=None, surface=None, **kwargs):
        super().__init__(*args, **kwargs)
        if surface:
            self.setProperty("cdbSurface", surface)
            self.setAttribute(Qt.WA_StyledBackground, True)
        self._bind_theme(theme)


class CdbMainWindow(StyledWidgetMixin, QMainWindow):
    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._bind_theme(theme)
        style = self.theme.current
        self.resize(style.metric("window_width"), style.metric("window_height"))
        self.setMinimumSize(style.metric("minimum_width"), style.metric("minimum_height"))
        self.setStatusBar(CdbStatusBar(self, theme=self.theme))


class CdbDialog(StyledWidgetMixin, QDialog):
    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._bind_theme(theme)
        style = self.theme.current
        self.resize(style.metric("dialog_width"), style.metric("dialog_height"))


class CdbLabel(StyledWidgetMixin, QLabel):
    def __init__(self, text="", parent=None, *, role=TextRole.BODY, padding_role=None, theme=None):
        super().__init__(text, parent)
        self.TEXT_ROLE = TextRole(role)
        self._padding_role = LayoutRole(padding_role) if padding_role is not None else None
        self.setProperty("cdbTextRole", self.TEXT_ROLE.value)
        self.setTextFormat(Qt.PlainText)
        self.setAlignment(CONTENT_ALIGNMENT)
        if self.TEXT_ROLE is TextRole.CAPTION:
            self.setWordWrap(True)
        self._bind_theme(theme)

    def apply_style(self, style):
        super().apply_style(style)
        if self._padding_role is not None:
            margins, _ = style.layout(self._padding_role)
            self.setContentsMargins(*margins)


class CdbButton(StyledWidgetMixin, QPushButton):
    CONTROL = True

    def __init__(self, text="", parent=None, *, variant=ButtonVariant.SECONDARY, icon=None, icon_rotation=0, theme=None):
        super().__init__(text, parent)
        self.variant = ButtonVariant(variant)
        self.icon_name = IconName(icon) if icon else None
        self.icon_rotation = icon_rotation
        self.setProperty("cdbVariant", self.variant.value)
        self.setCursor(Qt.PointingHandCursor)
        if self.variant is ButtonVariant.NAVIGATION:
            self.setCheckable(True)
        self._bind_theme(theme)
        self.toggled.connect(self._refresh_icon)

    @property
    def visual_state(self):
        if not self.isEnabled():
            return VisualState.DISABLED
        if self.isChecked():
            return VisualState.SELECTED_HOVER if self.underMouse() else VisualState.SELECTED
        return VisualState.HOVER if self.underMouse() else VisualState.NORMAL

    def apply_style(self, style):
        super().apply_style(style)
        metric = "nav_icon" if self.variant is ButtonVariant.NAVIGATION else "icon"
        self.setIconSize(QSize(style.metric(metric), style.metric(metric)))
        self._refresh_icon()

    def _refresh_icon(self, *_):
        if self.icon_name is None:
            return
        color = self.theme.current.button_colors(self.variant, self.visual_state)[1]
        variant = IconVariant.SOLID if self.isChecked() else IconVariant.OUTLINE
        self.setIcon(self.theme.icons.icon(self.icon_name, color, variant, rotation=self.icon_rotation))

    def set_icon_rotation(self, rotation):
        self.icon_rotation = rotation
        self._refresh_icon()

    def event(self, event):
        result = super().event(event)
        if event.type() in ICON_EVENTS and hasattr(self, "theme"):
            self._refresh_icon()
        return result


class CdbNavigationButton(CdbButton):
    def __init__(self, text, parent=None, *, icon, theme=None):
        self.label_text = text
        super().__init__(text, parent, variant=ButtonVariant.NAVIGATION, icon=icon, theme=theme)
        self.setAccessibleName(text)

    def set_compact(self, compact):
        self.setText("" if compact else self.label_text)
        self.setToolTip(self.label_text if compact else "")
        self.setProperty("cdbCompact", compact)
        self.style().unpolish(self)
        self.style().polish(self)
        self.updateGeometry()


class CdbFieldActionButton(CdbButton):
    CONTROL = False

    def __init__(self, text, parent=None, *, icon=None, theme=None):
        super().__init__(text, parent, variant=ButtonVariant.FIELD_ACTION, icon=icon, theme=theme)


class CdbLineEdit(StyledWidgetMixin, QLineEdit):
    CONTROL = True

    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAlignment(CONTENT_ALIGNMENT)
        self._bind_theme(theme)


class CdbActionLineEdit(CdbLineEdit):
    """A text field and its action share one outer border and focus surface."""

    def __init__(self, text="", parent=None, *, action_text, action_icon, theme=None):
        super().__init__(text, parent, theme=theme)
        self.action_button = CdbFieldActionButton(action_text, self, icon=action_icon, theme=self.theme)
        self.theme.changed.connect(self._layout_action)
        self._layout_action()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "action_button"):
            self._layout_action()

    def _layout_action(self, *_):
        style = self.theme.current
        width = max(style.metric("field_button_width"), self.action_button.sizeHint().width())
        self.action_button.setGeometry(self.width() - width, 0, width, max(0, self.height() - style.metric("border")))
        self.setTextMargins(0, 0, width, 0)


class CdbColorEdit(CdbActionLineEdit):
    """An optional HEX override with an integrated, themed color picker."""

    colorCommitted = Signal(str)

    def __init__(self, title, parent=None, *, theme=None):
        super().__init__(parent=parent, action_text=COLOR_PICKER_ACTION, action_icon=IconName.EDIT, theme=theme)
        self._title = title
        self.setAccessibleName(title)
        self.action_button.setAccessibleName(f"{title} {COLOR_PICKER_ACTION}")
        self.editingFinished.connect(lambda: self.colorCommitted.emit(self.text()))
        self.action_button.clicked.connect(self._choose_color)

    def _choose_color(self):
        initial = QColor(self.text())
        if not initial.isValid():
            initial = QColor(self.placeholderText())
        dialog = CdbColorDialog(initial, self, theme=self.theme)
        dialog.setWindowTitle(self._title)
        if dialog.exec() == QDialog.Accepted:
            self.setText(dialog.selectedColor().name(QColor.HexRgb))
            self.colorCommitted.emit(self.text())
        dialog.deleteLater()


class CdbComboBox(StyledWidgetMixin, QComboBox):
    CONTROL = True

    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(COMBO_MINIMUM_CHARACTERS)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setItemDelegate(QStyledItemDelegate(self))
        self.currentTextChanged.connect(self.setToolTip)
        self._bind_theme(theme)

    def add_icon_item(self, text, icon, user_data=None):
        name = IconName(icon)
        index = self.count()
        self.addItem(text, user_data)
        self.setItemData(index, name.value, COMBO_ICON_ROLE)
        self._refresh_item_icon(index, self.theme.current)

    def _refresh_item_icon(self, index, style):
        name = self.itemData(index, COMBO_ICON_ROLE)
        if name is not None:
            color = style.colors["text" if self.isEnabled() else "disabled_text"]
            self.setItemIcon(index, self.theme.icons.icon(name, color))

    def apply_style(self, style):
        super().apply_style(style)
        size = QSize(style.metric("icon"), style.metric("icon"))
        self.setIconSize(size)
        self.view().setIconSize(size)
        self.view().setFont(self.font())
        for index in range(self.count()):
            self._refresh_item_icon(index, style)
        if self.lineEdit() is not None:
            self.lineEdit().setFont(self.font())

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        area = self.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxArrow, self)
        if not self.isEnabled():
            state = VisualState.DISABLED
        elif self.hasFocus() or self.view().isVisible():
            state = VisualState.SELECTED
        else:
            state = VisualState.HOVER if self.underMouse() else VisualState.NORMAL
        painter = QPainter(self)
        _paint_field_icon(painter, self, area, state)
        painter.end()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.EnabledChange and hasattr(self, "theme"):
            for index in range(self.count()):
                self._refresh_item_icon(index, self.theme.current)
        if event.type() in ICON_EVENTS:
            self.update()
        return result


class CdbDoubleSpinBox(StyledWidgetMixin, QDoubleSpinBox):
    CONTROL = True
    STEP_ICONS = ((QStyle.SC_SpinBoxUp, QDoubleSpinBox.StepUpEnabled, 180),
                  (QStyle.SC_SpinBoxDown, QDoubleSpinBox.StepDownEnabled, 0))

    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setKeyboardTracking(False)
        self.setAlignment(CONTENT_ALIGNMENT)
        self._bind_theme(theme)

    def apply_style(self, style):
        super().apply_style(style)
        self.lineEdit().setFont(self.font())

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        painter = QPainter(self)
        for control, flag, rotation in self.STEP_ICONS:
            area = self.style().subControlRect(QStyle.CC_SpinBox, option, control, self)
            if not self.isEnabled() or not self.stepEnabled() & flag:
                state = VisualState.DISABLED
            elif option.activeSubControls & control:
                state = VisualState.HOVER
            else:
                state = VisualState.NORMAL
            _paint_field_icon(painter, self, area, state, metric="spin_icon", rotation=rotation)
        painter.end()


class CdbPlainTextEdit(StyledWidgetMixin, QPlainTextEdit):
    def __init__(self, *args, theme=None, code=False, compact=False, role=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.TEXT_ROLE = TextRole(role) if role is not None else TextRole.CODE if code else TextRole.BODY
        self._compact = compact
        self._height_metric = "compact_editor_height" if compact else "editor_height"
        self._bind_theme(theme)

    def apply_style(self, style):
        super().apply_style(style)
        height = max(style.metric(self._height_metric), QFontMetrics(self.font()).lineSpacing() * EDITOR_MINIMUM_LINES)
        if self._compact:
            self.setFixedHeight(height)
        else:
            self.setMinimumHeight(height)


class CdbSqlEditor(CdbPlainTextEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, role=TextRole.SQL_CODE, **kwargs)


class CdbCard(StyledWidgetMixin, QFrame):
    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setProperty("cdbSurface", "card")
        self._bind_theme(theme)


class CdbSidebar(CdbWidget):
    collapsedChanged = Signal(bool)

    def __init__(self, *args, **kwargs):
        self._collapsed = False
        self._expanded_width = None
        self._navigation = []
        self._expanded_widgets = []
        super().__init__(*args, surface="panel", **kwargs)
        self.toggle_button = CdbButton("", self, variant=ButtonVariant.NAVIGATION,
                                       icon=IconName.CHEVRON_DOWN, theme=self.theme)
        self.toggle_button.setObjectName("sidebar_toggle")
        self.toggle_button.setProperty("cdbCompact", True)
        self.toggle_button.setCheckable(False)
        self.toggle_button.clicked.connect(lambda: self.set_collapsed(not self.collapsed))
        self._update_toggle()

    @property
    def collapsed(self):
        return self._collapsed

    def register_navigation(self, button):
        self._navigation.append(button)
        button.set_compact(self.collapsed)

    def register_expanded_widget(self, widget):
        self._expanded_widgets.append(widget)
        widget.setVisible(not self.collapsed)

    def set_collapsed(self, collapsed):
        if self._collapsed == bool(collapsed):
            return
        self._collapsed = bool(collapsed)
        for button in self._navigation:
            button.set_compact(self.collapsed)
        for widget in self._expanded_widgets:
            widget.setVisible(not self.collapsed)
        self._update_toggle()
        self.apply_style(self.theme.current)
        self.collapsedChanged.emit(self.collapsed)

    def _update_toggle(self):
        self.toggle_button.setToolTip(SIDEBAR_TEXT[self.collapsed])
        self.toggle_button.setAccessibleName(SIDEBAR_TEXT[self.collapsed])
        self.toggle_button.set_icon_rotation(SIDEBAR_ICON_ROTATIONS[self.collapsed])

    def remember_expanded_width(self, width):
        if not self.collapsed:
            self._expanded_width = width / self.theme.preferences.ui_scale

    def preferred_width(self):
        style = self.theme.current
        if self.collapsed:
            return style.metric("sidebar_collapsed_width")
        width = style.metric("sidebar_width") if self._expanded_width is None else round(self._expanded_width * style.preferences.ui_scale)
        return max(self.minimumWidth(), min(self.maximumWidth(), width))

    def apply_style(self, style):
        super().apply_style(style)
        if self.collapsed:
            self.setFixedWidth(style.metric("sidebar_collapsed_width"))
            return
        # Font changes can require more width than geometric scaling alone.
        metrics = QFontMetrics(self.theme.font(style=style))
        longest = max((metrics.horizontalAdvance(button.label_text) + style.metric("nav_icon") for button in self._navigation), default=0)
        margins, spacing = style.layout(LayoutRole.SIDEBAR)
        content_width = longest + margins[0] + margins[2] + 2 * (style.metric("padding_x") + style.metric("border")) + spacing
        minimum = max(style.metric("sidebar_min_width"), content_width)
        self.setMinimumWidth(minimum)
        self.setMaximumWidth(max(minimum, style.metric("sidebar_max_width")))


class CdbScrollArea(StyledWidgetMixin, QScrollArea):
    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self._bind_theme(theme)


class CdbStackedWidget(StyledWidgetMixin, QStackedWidget):
    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._bind_theme(theme)


class CdbTabWidget(StyledWidgetMixin, QTabWidget):
    """One rounded frame encloses both the native tab bar and its pages."""

    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._separator_geometry = None
        self.tabBar().setDrawBase(False)
        self._bind_theme(theme)
        self.tabBar().installEventFilter(self)

    def apply_style(self, style):
        super().apply_style(style)
        self.tabBar().setFont(self.font())
        # Qt lays out the bar independently of QWidget.contentsMargins(). The
        # stack owns page insets so its children never cover the frame corners.
        stack = self.findChild(QStackedWidget, options=Qt.FindDirectChildrenOnly)
        if stack is not None:
            inset = style.metric("radius") + style.metric("border")
            stack.setContentsMargins(inset, inset, inset, inset)
        self.update()

    def _tab_separator_geometry(self):
        bar = self.tabBar()
        if not bar.isVisible() or not bar.count():
            return None
        width = self.theme.current.metric("border")
        position = self.tabPosition()
        horizontal = position in (QTabWidget.North, QTabWidget.South)
        if horizontal:
            y = bar.geometry().bottom() - width + 1 if position == QTabWidget.North else bar.y()
            line = QRect(width, y, max(0, self.width() - 2 * width), width)
        else:
            x = bar.geometry().right() - width + 1 if position == QTabWidget.West else bar.x()
            line = QRect(x, width, width, max(0, self.height() - 2 * width))
        active = QRect()
        if bar.currentIndex() >= 0:
            # Keep the untrimmed rect: scrolling must clip a corner, not create
            # a new rounded corner at the viewport edge.
            active = bar.tabRect(bar.currentIndex()).translated(bar.pos())
        return line, active

    def eventFilter(self, watched, event):
        if watched is self.tabBar() and event.type() in TAB_BAR_GEOMETRY_EVENTS:
            # Scrolling and dragging can move tabs without moving the bar. Only
            # repaint when geometry changes, avoiding a parent/child paint loop.
            if self._tab_separator_geometry() != self._separator_geometry:
                self.update()
        return super().eventFilter(watched, event)

    def _paint_tab_header(self, painter, style, rect, radius, geometry):
        line, active = geometry
        position = self.tabPosition()
        header = QRectF(rect)
        if position == QTabWidget.North:
            header.setBottom(line.bottom() + 1)
        elif position == QTabWidget.South:
            header.setTop(line.top())
        elif position == QTabWidget.West:
            header.setRight(line.right() + 1)
        else:
            header.setLeft(line.left())
        painter.fillRect(header, QColor(style.colors["secondary_surface"]))
        if not active.isValid():
            return

        # Work in top-tab coordinates, then rotate for other native positions.
        transform = QTransform()
        if position == QTabWidget.North:
            transform.translate(active.left(), active.top())
        elif position == QTabWidget.South:
            transform.translate(active.right() + 1, active.bottom() + 1).rotate(180)
        elif position == QTabWidget.West:
            transform.translate(active.left(), active.bottom() + 1).rotate(-90)
        else:
            transform.translate(active.right() + 1, active.top()).rotate(90)
        horizontal = position in (QTabWidget.North, QTabWidget.South)
        length, depth = (active.width(), active.height()) if horizontal else (active.height(), active.width())
        width = style.metric("border")
        left, top, right, bottom = width / 2, width / 2, length - width / 2, depth
        curve = min(radius, (right - left) / 2, bottom - top)
        left_side = QPainterPath()
        left_side.moveTo(left, bottom)
        left_side.lineTo(left, top + curve)
        left_side.arcTo(QRectF(left, top, 2 * curve, 2 * curve), 180, -90)
        right_side = QPainterPath()
        right_side.moveTo(right - curve, top)
        right_side.arcTo(QRectF(right - 2 * curve, top, 2 * curve, 2 * curve), 90, -90)
        right_side.lineTo(right, bottom)
        fill = QPainterPath(left_side)
        fill.connectPath(right_side)
        fill.closeSubpath()
        painter.fillPath(transform.map(fill), QColor(style.colors["surface"]))

        # The outer frame owns the shared top and any coincident side. Draw
        # only the remaining tab sides so no edge receives a second stroke.
        local_outer = transform.inverted()[0].mapRect(rect)
        if left > local_outer.left():
            painter.drawPath(transform.map(left_side))
        if right < local_outer.right():
            painter.drawPath(transform.map(right_side))

    def paintEvent(self, event):
        super().paintEvent(event)
        style = self.theme.current
        width = style.metric("border")
        offset = width / 2
        rect = QRectF(self.rect()).adjusted(offset, offset, -offset, -offset)
        radius = max(0, style.metric("radius") - offset)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(style.colors["border"]), width)
        pen.setCapStyle(Qt.FlatCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        outline = QPainterPath()
        outline.addRoundedRect(rect, radius, radius)
        painter.fillPath(outline, QColor(style.colors["surface"]))
        self._separator_geometry = self._tab_separator_geometry()
        if self._separator_geometry is not None:
            painter.save()
            painter.setClipPath(outline, Qt.IntersectClip)
            self._paint_tab_header(painter, style, rect, radius, self._separator_geometry)
            line, active = self._separator_geometry
            painter.setClipRegion(QRegion(line).subtracted(QRegion(active)), Qt.IntersectClip)
            painter.fillRect(line, QColor(style.colors["border"]))
            painter.restore()
        painter.drawPath(outline)
        painter.end()


class CdbSplitter(StyledWidgetMixin, QSplitter):
    HANDLE_METRIC = "splitter_handle"

    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setChildrenCollapsible(False)
        self._bind_theme(theme)

    def apply_style(self, style):
        super().apply_style(style)
        self.setHandleWidth(style.metric(self.HANDLE_METRIC))


class CdbSectionSplitterHandle(StyledWidgetMixin, QSplitterHandle):
    """The resize hit area stays wide; only its central divider is visible."""

    def __init__(self, orientation, parent, *, theme=None):
        super().__init__(orientation, parent)
        self._bind_theme(theme)

    def paintEvent(self, event):
        painter = QPainter(self)
        line_orientation = Qt.Vertical if self.orientation() == Qt.Horizontal else Qt.Horizontal
        _paint_divider(painter, self.rect(), line_orientation, self.theme.current, active=self.underMouse())
        painter.end()

    def event(self, event):
        result = super().event(event)
        if event.type() in ICON_EVENTS:
            self.update()
        return result


class CdbSectionSplitter(CdbSplitter):
    """Resizable page sections with the same gutter as static section layouts."""

    HANDLE_METRIC = "section_gap"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setProperty("cdbSectionSplit", True)

    def createHandle(self):
        return CdbSectionSplitterHandle(self.orientation(), self, theme=getattr(self, "theme", None))

    def sizeHint(self):
        # Wrapped page headings make QScrollArea consult height-for-width.
        # Prefer the usable minimum here so editors/tables can share the actual
        # remaining height instead of forcing the page to their preferred size.
        hint = super().sizeHint()
        hint.setHeight(self.minimumSizeHint().height())
        return hint


class CdbSidebarSplitterHandle(CdbSectionSplitterHandle):
    def paintEvent(self, event):
        painter = QPainter(self)
        style = self.theme.current
        middle = self.rect().center().x()
        painter.fillRect(self.rect(), style.colors["window"])
        painter.fillRect(QRect(0, 0, middle, self.height()), style.colors["sidebar"])
        _paint_divider(painter, self.rect(), Qt.Vertical, style, padding_metric="zero", active=self.isEnabled() and self.underMouse())
        painter.end()


class CdbSidebarSplitter(CdbSplitter):
    """Resizable navigation with a remembered expanded width, independent of page layout."""

    HANDLE_METRIC = "sidebar_resize_handle"

    def __init__(self, sidebar, pages, parent=None, *, theme=None):
        self.sidebar = sidebar
        self._restoring = False
        super().__init__(Qt.Horizontal, parent, theme=theme)
        self.addWidget(sidebar)
        self.addWidget(pages)
        self.setStretchFactor(0, 0)
        self.setStretchFactor(1, 1)
        self.splitterMoved.connect(self._remember_width)
        sidebar.collapsedChanged.connect(self.restore_sidebar_width)
        self.theme.changed.connect(self.restore_sidebar_width)
        self.restore_sidebar_width()

    def createHandle(self):
        return CdbSidebarSplitterHandle(self.orientation(), self, theme=getattr(self, "theme", None))

    def _remember_width(self, *_):
        if not self._restoring:
            self.sidebar.remember_expanded_width(self.sizes()[0])

    def restore_sidebar_width(self, *_):
        if self.count() < 2 or self._restoring:
            return
        self._restoring = True
        try:
            width = self.sidebar.preferred_width()
            self.setSizes([width, max(0, self.width() - self.handleWidth() - width)])
            self.handle(1).setEnabled(not self.sidebar.collapsed)
            self.handle(1).setCursor(Qt.ArrowCursor if self.sidebar.collapsed else Qt.SplitHCursor)
        finally:
            self._restoring = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.restore_sidebar_width()


class TableStyleMixin(StyledWidgetMixin):
    def set_embedded(self, embedded: bool):
        """Let an enclosing container own this table's frame and outer inset."""
        self.setProperty("cdbEmbedded", embedded)
        self.style().unpolish(self)
        self.style().polish(self)
        self.updateGeometry()
        self.viewport().update()

    def apply_style(self, style):
        super().apply_style(style)
        self.horizontalHeader().setFont(self.font())
        self.verticalHeader().setFont(self.font())
        row_height = max(style.metric("table_row"), QFontMetrics(self.font()).height() + 2 * style.metric("padding_y"))
        self.verticalHeader().setMinimumSectionSize(row_height)
        self.verticalHeader().setDefaultSectionSize(row_height)
        self.setMinimumHeight(row_height * TABLE_MINIMUM_ROWS)

    def _table_defaults(self, theme):
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.horizontalHeader().setDefaultAlignment(CONTENT_ALIGNMENT)
        self.horizontalHeader().setStretchLastSection(True)
        self._bind_theme(theme)


class CdbTableView(TableStyleMixin, QTableView):
    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._table_defaults(theme)


class CdbTableWidget(TableStyleMixin, QTableWidget):
    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._table_defaults(theme)


class CdbTreeWidget(StyledWidgetMixin, QTreeWidget):
    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.header().setDefaultAlignment(CONTENT_ALIGNMENT)
        self._bind_theme(theme)

    def apply_style(self, style):
        super().apply_style(style)
        self.header().setFont(self.theme.font(TextRole.SECTION_HEADING, style))


class CdbStatusBar(StyledWidgetMixin, QStatusBar):
    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._bind_theme(theme)


class CdbLogBar(CdbStatusBar):
    """A one-line status surface activated anywhere by mouse or keyboard."""

    activated = Signal()
    ACTIVATION_KEYS = frozenset((Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space))

    def __init__(self, text="", parent=None, *, theme=None):
        super().__init__(parent, theme=theme)
        self._pressed = False
        self.setProperty("cdbClickable", True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizeGripEnabled(False)
        self.message_label = CdbLabel(text, self, theme=self.theme)
        self.message_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.message_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.addWidget(self.message_label, 1)

    def apply_style(self, style):
        super().apply_style(style)
        self.setFixedHeight(QFontMetrics(self.font()).height() + 2 * style.metric("log_padding_y") + style.metric("border"))
        self.layout().setContentsMargins(style.metric("padding_x"), 0, style.metric("padding_x"), 0)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.setFocus(Qt.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            activate = self._pressed and self.rect().contains(event.position().toPoint())
            self._pressed = False
            event.accept()
            if activate:
                self.activated.emit()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in self.ACTIVATION_KEYS:
            if not event.isAutoRepeat():
                self.activated.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class StyledLayoutMixin:
    def _bind_layout(self, role, theme=None):
        self.role = LayoutRole(role)
        self.theme = theme or ThemeManager.current_manager()
        self.theme.changed.connect(self.apply_style)
        self.apply_style(self.theme.current)

    def apply_style(self, style):
        margins, spacing = style.layout(self.role)
        self.setContentsMargins(*margins)
        self.setSpacing(spacing)
        self.invalidate()


class CdbVBoxLayout(StyledLayoutMixin, QVBoxLayout):
    def __init__(self, *args, role=LayoutRole.ROW, theme=None):
        super().__init__(*args)
        self._bind_layout(role, theme)


class CdbHBoxLayout(StyledLayoutMixin, QHBoxLayout):
    def __init__(self, *args, role=LayoutRole.ROW, theme=None):
        super().__init__(*args)
        # Pack ordinary controls to the left; explicit stretches still reserve
        # the remaining width for header titles and right-side actions.
        self.setAlignment(CONTENT_HORIZONTAL_ALIGNMENT)
        self._bind_layout(role, theme)


class CdbFormLayout(StyledLayoutMixin, QFormLayout):
    def __init__(self, *args, role=LayoutRole.FORM, theme=None):
        super().__init__(*args)
        self.setFormAlignment(CONTENT_HORIZONTAL_ALIGNMENT | Qt.AlignTop)
        self.setLabelAlignment(CONTENT_ALIGNMENT)
        self.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self._bind_layout(role, theme)

    def addRow(self, *args):
        if args and isinstance(args[0], str):
            args = (CdbLabel(args[0], theme=self.theme), *args[1:])
        return super().addRow(*args)


class CdbDivider(CdbWidget):
    """A static separator using the same gutter and line as section splitters."""

    def __init__(self, orientation=Qt.Horizontal, parent=None, *, theme=None):
        self.orientation = orientation
        super().__init__(parent, theme=theme)

    def apply_style(self, style):
        super().apply_style(style)
        if self.orientation == Qt.Horizontal:
            self.setFixedHeight(style.metric("section_gap"))
        else:
            self.setFixedWidth(style.metric("section_gap"))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        _paint_divider(painter, self.rect(), self.orientation, self.theme.current)
        painter.end()


class CdbSectionGroup(CdbWidget):
    """Arrange regions and insert a divider only between neighboring regions."""

    def __init__(self, orientation=Qt.Vertical, parent=None, *, theme=None):
        super().__init__(parent, theme=theme)
        self.orientation = orientation
        layout_type = CdbVBoxLayout if orientation == Qt.Vertical else CdbHBoxLayout
        self.content = layout_type(self, role=LayoutRole.FLUSH, theme=self.theme)

    def add_section(self, widget, stretch=0):
        if self.content.count():
            orientation = Qt.Horizontal if self.orientation == Qt.Vertical else Qt.Vertical
            self.content.addWidget(CdbDivider(orientation, theme=self.theme))
        self.content.addWidget(widget, stretch)


class CdbSection(CdbWidget):
    """A borderless region with a shared title/action header and content padding."""

    def __init__(self, title, parent=None, *, theme=None):
        super().__init__(parent, theme=theme, surface="section")
        self.content = CdbVBoxLayout(self, role=LayoutRole.SECTION, theme=self.theme)
        self.header = CdbHBoxLayout(theme=self.theme)
        self.heading = CdbLabel(title, role=TextRole.SECTION_HEADING, theme=self.theme)
        self.heading.setWordWrap(True)
        self.header.addWidget(self.heading, 1)
        self.content.addLayout(self.header)


class CdbTitleArea(CdbWidget):
    """A page or panel title, description and optional right-side actions."""

    def __init__(self, title, subtitle, parent=None, *, eyebrow=None,
                 title_role=TextRole.HEADING, subtitle_role=TextRole.MUTED, theme=None):
        super().__init__(parent, theme=theme, surface="section")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.content = CdbVBoxLayout(self, role=LayoutRole.TITLE, theme=self.theme)
        if eyebrow:
            self.content.addWidget(CdbLabel(eyebrow, role=TextRole.EYEBROW, theme=self.theme))
        self.header = CdbHBoxLayout(theme=self.theme)
        self.heading = CdbLabel(title, role=title_role, theme=self.theme)
        self.heading.setWordWrap(True)
        self.header.addWidget(self.heading, 1)
        self.content.addLayout(self.header)
        self.description = CdbLabel(subtitle, role=subtitle_role, theme=self.theme)
        self.description.setWordWrap(True)
        self.content.addWidget(self.description)


class CdbPage(CdbWidget):
    def __init__(self, title, subtitle, parent=None, *, theme=None):
        super().__init__(parent, theme=theme, surface="window")
        self.content = CdbVBoxLayout(self, role=LayoutRole.PAGE, theme=self.theme)
        self.title_area = CdbTitleArea(title, subtitle, eyebrow=APPLICATION_EYEBROW, theme=self.theme)
        self.content.addWidget(self.title_area)


class CdbDialogButtonBox(StyledWidgetMixin, QDialogButtonBox):
    def __init__(self, parent=None, *, theme=None):
        super().__init__(parent)
        self._bind_theme(theme)

    def add_action(self, text, role, *, icon=None, primary=False):
        button = CdbButton(text, icon=icon, variant=ButtonVariant.PRIMARY if primary else ButtonVariant.SECONDARY, theme=self.theme)
        self.addButton(button, role)
        return button


class CdbMessageBox(StyledWidgetMixin, QMessageBox):
    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setTextFormat(Qt.PlainText)
        self._bind_theme(theme)

    @classmethod
    def warning(cls, parent, title, text):
        box = cls(parent)
        box.setWindowTitle(title)
        box.setText(text)
        button = CdbButton(BUTTON_TEXT[QMessageBox.Ok], icon=IconName.CHECK, theme=box.theme)
        box.addButton(button, QMessageBox.AcceptRole)
        box.exec()

    @classmethod
    def question(cls, parent, title, text, buttons=None, defaultButton=None):
        box = cls(parent)
        box.setWindowTitle(title)
        box.setText(text)
        yes = CdbButton(BUTTON_TEXT[QMessageBox.Yes], icon=IconName.CHECK, theme=box.theme)
        no = CdbButton(BUTTON_TEXT[QMessageBox.No], icon=IconName.CLOSE, theme=box.theme)
        box.addButton(yes, QMessageBox.YesRole)
        box.addButton(no, QMessageBox.NoRole)
        box.setDefaultButton(no)
        box.exec()
        return QMessageBox.Yes if box.clickedButton() is yes else QMessageBox.No


class CdbMenu(StyledWidgetMixin, QMenu):
    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._bind_theme(theme)


class CdbColorDialog(StyledWidgetMixin, QColorDialog):
    def __init__(self, color, parent=None, *, theme=None):
        super().__init__(color, parent)
        self.setOption(QColorDialog.DontUseNativeDialog, True)
        self._bind_theme(theme)


class CdbFileDialog(StyledWidgetMixin, QFileDialog):
    def __init__(self, *args, theme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.setOption(QFileDialog.DontUseNativeDialog, True)
        self._bind_theme(theme)

    @classmethod
    def getSaveFileName(cls, parent=None, caption="", directory="", filter=""):
        dialog = cls(parent, caption, directory, filter)
        dialog.setAcceptMode(QFileDialog.AcceptSave)
        if dialog.exec() == QDialog.Accepted:
            return dialog.selectedFiles()[0], dialog.selectedNameFilter()
        return "", ""
