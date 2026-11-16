"""A typed result table with independent Excel-style column filters and sorting."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

from qtpy.QtCore import QPoint, QRect, QSortFilterProxyModel, Qt, Signal
from qtpy.QtGui import QFontMetrics
from qtpy.QtWidgets import QHeaderView, QStyle, QStyleOptionHeader, QTreeWidgetItem

from cdw.domain.contracts import QueryResult
from cdw.domain.filtering import (
    ColumnFilter, ColumnKind, FilterOperator, OPERAND_FREE_OPERATORS,
    column_kind, compile_filter, distinct_values, is_unordered, operators_for, sort_key, typed_value,
)
from cdw.ui.design.icons import IconName
from cdw.ui.design.spec import ButtonVariant, LayoutRole, TextRole
from cdw.ui.models import ResultTableModel
from cdw.ui.widgets import (
    CdwButton, CdwComboBox, CdwDialog, CdwFormLayout, CdwHBoxLayout,
    CdwLabel, CdwLineEdit, CdwScrollArea, CdwTableView, CdwTreeWidget,
    CdwVBoxLayout, CdwWidget, StyledWidgetMixin,
)

HEADER_RESIZE_GRIP = 4
HEADER_MINIMUM_CHARACTERS = 6
SORT_SUFFIXES = {Qt.AscendingOrder: " ↑", Qt.DescendingOrder: " ↓"}
OPERATOR_LABELS = {
    FilterOperator.ANY: "조건 없음", FilterOperator.EQUAL: "같음", FilterOperator.NOT_EQUAL: "같지 않음",
    FilterOperator.GREATER_EQUAL: "이상 (≥)", FilterOperator.LESS_EQUAL: "이하 (≤)",
    FilterOperator.GREATER: "초과 (>)", FilterOperator.LESS: "미만 (<)", FilterOperator.BETWEEN: "사이 (양 끝 포함)",
    FilterOperator.CONTAINS: "문자 포함", FilterOperator.STARTS_WITH: "문자로 시작", FilterOperator.ENDS_WITH: "문자로 끝남",
    FilterOperator.REGEX: "정규 표현식", FilterOperator.IS_NULL: "NULL인 값", FilterOperator.NOT_NULL: "NULL이 아닌 값",
    FilterOperator.IS_EMPTY: "빈 문자열",
}
KIND_LABELS = {
    ColumnKind.NUMBER: "숫자", ColumnKind.DATE: "날짜", ColumnKind.DATETIME: "날짜·시각",
    ColumnKind.TIME: "시각", ColumnKind.TEXT: "문자", ColumnKind.BOOLEAN: "논리값", ColumnKind.OTHER: "값 선택",
}
INPUT_LABELS = {
    ColumnKind.NUMBER: ("숫자", "최솟값", "최댓값"),
    ColumnKind.DATE: ("날짜", "시작 날짜", "끝 날짜"),
    ColumnKind.DATETIME: ("날짜·시각", "시작 일시", "끝 일시"),
    ColumnKind.TIME: ("시각", "시작 시각", "끝 시각"),
    ColumnKind.TEXT: ("문자", "문자", "문자"),
    ColumnKind.BOOLEAN: ("논리값", "논리값", "논리값"),
    ColumnKind.OTHER: ("값", "값", "값"),
}
TEMPORAL_OPERATOR_LABELS = {
    FilterOperator.GREATER_EQUAL: "이후 (포함, ≥)", FilterOperator.LESS_EQUAL: "이전 (포함, ≤)",
    FilterOperator.GREATER: "이후 (제외, >)", FilterOperator.LESS: "이전 (제외, <)",
}
TEMPORAL_KINDS = frozenset((ColumnKind.DATE, ColumnKind.DATETIME, ColumnKind.TIME))
BOOLEAN_CHOICES = (("참 (true)", "true"), ("거짓 (false)", "false"))
KIND_HELP = {
    ColumnKind.NUMBER: "숫자 크기를 비교합니다. 범위는 양 끝 값을 포함합니다.",
    ColumnKind.DATE: "날짜 조건 또는 아래 연·월·일 목록으로 선택합니다.",
    ColumnKind.DATETIME: "일시를 비교합니다. 날짜만 입력하면 해당 날짜의 00:00입니다.",
    ColumnKind.TIME: "시각을 비교합니다. 소수 초와 시간대(+09:00 등)를 입력할 수 있습니다.",
    ColumnKind.TEXT: "문자 포함·시작·끝 조건은 대소문자를 구분하지 않습니다. 정규식도 사용할 수 있습니다.",
    ColumnKind.BOOLEAN: "참/거짓을 선택하거나 NULL 여부로 필터링합니다.",
    ColumnKind.OTHER: "아래 값 목록에서 선택하거나 NULL 여부로 필터링합니다.",
}
TEXT = {
    "title": "{name} · {kind} 필터", "ascending": "오름차순", "descending": "내림차순", "unsort": "정렬 해제",
    "type": "자료형: {type}",
    "condition": "조건", "first": "값 / 시작", "second": "끝", "search": "값 검색",
    "all": "전체 값", "select": "검색 결과 선택", "deselect": "선택 해제",
    "apply": "적용", "cancel": "취소", "reset": "필터 해제", "empty": "선택할 값이 없습니다.",
    "date": "YYYY-MM-DD", "datetime": "YYYY-MM-DD HH:MM:SS", "time": "HH:MM:SS", "number": "숫자 입력",
    "text": "값 입력", "regex": "정규 표현식 · (?i)로 대소문자 무시",
    "tip": "열 필터/정렬 · Alt+↓",
    "year": "{year}년", "month": "{month:02d}월", "day": "{day:02d}일",
}


class ResultFilterProxyModel(QSortFilterProxyModel):
    """Filter specifications are validated atomically before the view changes."""

    changed = Signal()

    def __init__(self, source: ResultTableModel, parent=None):
        super().__init__(parent)
        self._filters: dict[int, ColumnFilter] = {}
        self._predicates = {}
        self._choices = {}
        self._sort_order = Qt.AscendingOrder
        self.setDynamicSortFilter(False)
        self.setSourceModel(source)
        source.modelReset.connect(self._reset)

    @property
    def result(self) -> QueryResult:
        return self.sourceModel().result

    def kind(self, column: int) -> ColumnKind:
        return column_kind(self.result.types[column])

    def filter_for(self, column: int) -> ColumnFilter:
        return self._filters.get(column, ColumnFilter())

    def choices(self, column: int):
        if column not in self._choices:
            self._choices[column] = distinct_values((row[column] for row in self.result.rows), self.kind(column))
        return self._choices[column]

    def apply_filter(self, column: int, specification: ColumnFilter):
        if not 0 <= column < self.sourceModel().columnCount():
            raise IndexError(column)
        predicate = compile_filter(self.kind(column), specification)
        if specification.active:
            self._filters[column] = specification
            self._predicates[column] = predicate
        else:
            self._filters.pop(column, None)
            self._predicates.pop(column, None)
        self.invalidateFilter()
        self.changed.emit()

    def clear_filter(self, column: int):
        self.apply_filter(column, ColumnFilter())

    def filterAcceptsRow(self, source_row, source_parent):
        result = self.result
        return result is not None and all(predicate(result.rows[source_row][column]) for column, predicate in self._predicates.items())

    def lessThan(self, left, right):
        first = self.result.rows[left.row()][left.column()]
        second = self.result.rows[right.row()][right.column()]
        first_unordered, second_unordered = is_unordered(first), is_unordered(second)
        if first_unordered != second_unordered:
            # Qt reverses comparisons for descending sort; keep NULL/NaN last in both directions.
            return first_unordered if self._sort_order == Qt.DescendingOrder else second_unordered
        first_key = sort_key(first, self.kind(left.column()))
        second_key = sort_key(second, self.kind(right.column()))
        if first_key == second_key:
            return left.row() > right.row() if self._sort_order == Qt.DescendingOrder else left.row() < right.row()
        return first_key < second_key

    def sort(self, column, order=Qt.AscendingOrder):
        self._sort_order = order
        super().sort(column, order)
        self.changed.emit()

    def row_indices(self) -> tuple[int, ...]:
        return tuple(self.mapToSource(self.index(row, 0)).row() for row in range(self.rowCount()))

    def _reset(self):
        self._filters.clear()
        self._predicates.clear()
        self._choices.clear()
        self.sort(-1)


class CdwFilterHeader(StyledWidgetMixin, QHeaderView):
    """A common header boundary: native sizing plus a Heroicons filter button."""

    filterRequested = Signal(int)

    def __init__(self, proxy: ResultFilterProxyModel, parent=None, *, theme=None):
        super().__init__(Qt.Horizontal, parent)
        self.proxy = proxy
        self._hover_column = -1
        self._pressed_column = -1
        self.setSectionsClickable(True)
        self.setMouseTracking(True)
        self.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setToolTip(TEXT["tip"])
        self._bind_theme(theme)
        proxy.changed.connect(self.viewport().update)

    def apply_style(self, style):
        super().apply_style(style)
        minimum = (QFontMetrics(self.font()).horizontalAdvance("M" * HEADER_MINIMUM_CHARACTERS)
                   + style.metric("filter_header_button_width") + 2 * style.metric("padding_y"))
        self.setMinimumSectionSize(minimum)
        self.setMinimumHeight(max(style.metric("control_height"), QFontMetrics(self.font()).height() + 2 * style.metric("padding_y")))
        self.viewport().update()

    def sectionSizeFromContents(self, logical_index):
        size = super().sectionSizeFromContents(logical_index)
        if hasattr(self, "theme"):
            font = QFontMetrics(self.font())
            text = self._column_text(logical_index)
            # Reserve sort text before sorting so a full heading does not become
            # elided when the user clicks its sort/filter controls.
            label_width = max(font.horizontalAdvance(text + suffix) for suffix in SORT_SUFFIXES.values())
            left, right = self._text_insets()
            size.setWidth(max(size.width(), label_width + left + right))
        return size

    def _column_text(self, column):
        return str(self.model().headerData(column, Qt.Horizontal, Qt.DisplayRole) or "") if self.model() else ""

    def _text_insets(self):
        style = self.theme.current
        padding = style.metric("padding_y")
        return padding, style.metric("filter_header_button_width") + HEADER_RESIZE_GRIP + padding

    def text_rect(self, column: int) -> QRect:
        left, right = self._text_insets()
        return QRect(self.sectionViewportPosition(column), 0, self.sectionSize(column), self.height()).adjusted(left, 0, -right, 0)

    def button_rect(self, column: int) -> QRect:
        width = self.theme.current.metric("filter_header_button_width")
        return QRect(self.sectionViewportPosition(column) + self.sectionSize(column) - width - HEADER_RESIZE_GRIP,
                     0, width, self.height())

    def paintSection(self, painter, rect, logical_index):
        if not rect.isValid():
            return
        painter.save()
        style = self.theme.current
        option = QStyleOptionHeader()
        self.initStyleOption(option)
        option.rect = rect
        option.section = logical_index
        option.text = ""
        self.style().drawControl(QStyle.CE_HeaderSection, option, painter, self)
        button = QRect(self.button_rect(logical_index))
        button.setTop(rect.top())
        button.setHeight(rect.height())
        if self._hover_column == logical_index:
            painter.fillRect(button, style.colors["hover"])
        active = self.proxy.filter_for(logical_index).active
        text = self._column_text(logical_index)
        if self.proxy.sortColumn() == logical_index:
            text += SORT_SUFFIXES[self.proxy.sortOrder()]
        text_rect = self.text_rect(logical_index)
        text_rect.setTop(rect.top())
        text_rect.setHeight(rect.height())
        painter.setFont(self.font())
        painter.setPen(style.colors["accent_text" if active else "muted"])
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         QFontMetrics(self.font()).elidedText(text, Qt.ElideRight, text_rect.width()))
        icon_size = min(style.metric("icon"), button.width(), button.height())
        icon_rect = QRect(0, 0, icon_size, icon_size)
        icon_rect.moveCenter(button.center())
        self.theme.icons.icon(IconName.CHEVRON_DOWN, style.colors["accent_text" if active else "muted"]).paint(painter, icon_rect)
        painter.restore()

    def mouseMoveEvent(self, event):
        column = self.logicalIndexAt(event.position().toPoint())
        hovered = column if column >= 0 and self.button_rect(column).contains(event.position().toPoint()) else -1
        if hovered != self._hover_column:
            self._hover_column = hovered
            self.setCursor(Qt.PointingHandCursor if hovered >= 0 else Qt.ArrowCursor)
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover_column = -1
        self.viewport().update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        column = self.logicalIndexAt(event.position().toPoint())
        if event.button() == Qt.LeftButton and column >= 0 and self.button_rect(column).contains(event.position().toPoint()):
            self._pressed_column = column
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pressed_column >= 0:
            column, self._pressed_column = self._pressed_column, -1
            if event.button() == Qt.LeftButton and self.button_rect(column).contains(event.position().toPoint()):
                self.filterRequested.emit(column)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class CdwResultTableView(CdwTableView):
    filterRequested = Signal(int)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Down and event.modifiers() == Qt.AltModifier and self.model().columnCount():
            self.filterRequested.emit(max(0, self.currentIndex().column()))
            event.accept()
            return
        super().keyPressEvent(event)


class ColumnFilterPopup(CdwDialog):
    """Edits a pending filter; only Apply changes the result table."""

    def __init__(self, proxy: ResultFilterProxyModel, column: int, parent=None, *, theme=None):
        super().__init__(parent, theme=theme)
        self.setWindowFlags(Qt.Popup)
        self.proxy = proxy
        self.column = column
        self.kind = proxy.kind(column)
        self.initial_filter = proxy.filter_for(column)
        self.values = proxy.choices(column)
        self.value_items = {}
        self._leaves = []
        self._all_keys = frozenset(value.key for value in self.values)
        self._initial_keys = self._all_keys if self.initial_filter.selected_keys is None else self.initial_filter.selected_keys
        self.setWindowTitle(TEXT["title"].format(name=proxy.result.columns[column], kind=KIND_LABELS[self.kind]))
        outer = CdwVBoxLayout(self, role=LayoutRole.DIALOG, theme=self.theme)
        heading = CdwLabel(self.windowTitle(), role=TextRole.SECTION_HEADING, theme=self.theme)
        heading.setWordWrap(True)
        outer.addWidget(heading)
        self.type_label = CdwLabel(TEXT["type"].format(type=proxy.result.types[column]),
                                   role=TextRole.MUTED, theme=self.theme)
        outer.addWidget(self.type_label)
        order = CdwHBoxLayout(theme=self.theme)
        self.ascending_button = CdwButton(TEXT["ascending"], theme=self.theme)
        self.descending_button = CdwButton(TEXT["descending"], theme=self.theme)
        self.unsort_button = CdwButton(TEXT["unsort"], theme=self.theme)
        for button in (self.ascending_button, self.descending_button, self.unsort_button):
            order.addWidget(button)
        outer.addLayout(order)
        self.ascending_button.clicked.connect(lambda: self._sort(Qt.AscendingOrder))
        self.descending_button.clicked.connect(lambda: self._sort(Qt.DescendingOrder))
        self.unsort_button.clicked.connect(self._unsort)

        self.scroll = CdwScrollArea(theme=self.theme)
        body = CdwWidget(theme=self.theme)
        content = CdwVBoxLayout(body, role=LayoutRole.FLUSH, theme=self.theme)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)
        self.condition_help = CdwLabel(KIND_HELP[self.kind], role=TextRole.MUTED, theme=self.theme)
        self.condition_help.setWordWrap(True)
        content.addWidget(self.condition_help)
        self.form = CdwFormLayout(role=LayoutRole.ROW, theme=self.theme)
        self.operator_combo = CdwComboBox(theme=self.theme)
        for operator in operators_for(self.kind):
            label = (TEMPORAL_OPERATOR_LABELS.get(operator, OPERATOR_LABELS[operator])
                     if self.kind in TEMPORAL_KINDS else OPERATOR_LABELS[operator])
            self.operator_combo.addItem(label, operator.value)
        self.lower_edit = CdwLineEdit(theme=self.theme)
        self.upper_edit = CdwLineEdit(theme=self.theme)
        self.boolean_combo = CdwComboBox(theme=self.theme)
        for label, value in BOOLEAN_CHOICES:
            self.boolean_combo.addItem(label, value)
        self.form.addRow(TEXT["condition"], self.operator_combo)
        self.form.addRow(TEXT["first"], self.lower_edit)
        self.form.addRow(TEXT["second"], self.upper_edit)
        self.form.addRow(KIND_LABELS[ColumnKind.BOOLEAN], self.boolean_combo)
        content.addLayout(self.form)
        self.search_edit = CdwLineEdit(theme=self.theme)
        self.search_edit.setPlaceholderText(TEXT["search"])
        content.addWidget(self.search_edit)
        selection = CdwHBoxLayout(theme=self.theme)
        self.select_button = CdwButton(TEXT["select"], theme=self.theme)
        self.deselect_button = CdwButton(TEXT["deselect"], theme=self.theme)
        selection.addWidget(self.select_button)
        selection.addWidget(self.deselect_button)
        content.addLayout(selection)
        self.values_tree = CdwTreeWidget(theme=self.theme)
        self.values_tree.setHeaderHidden(True)
        self.values_tree.setUniformRowHeights(True)
        self.values_tree.setSelectionMode(CdwTreeWidget.NoSelection)
        self.values_tree.setMinimumHeight(self.theme.current.metric("filter_values_height"))
        content.addWidget(self.values_tree, 1)
        self.error_label = CdwLabel(role=TextRole.ERROR, theme=self.theme)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        content.addWidget(self.error_label)
        self._build_values()
        actions = CdwHBoxLayout(theme=self.theme)
        self.reset_button = CdwButton(TEXT["reset"], icon=IconName.RESET, theme=self.theme)
        self.cancel_button = CdwButton(TEXT["cancel"], theme=self.theme)
        self.apply_button = CdwButton(TEXT["apply"], variant=ButtonVariant.PRIMARY, icon=IconName.CHECK, theme=self.theme)
        for button in (self.ascending_button, self.descending_button, self.unsort_button, self.select_button,
                       self.deselect_button, self.reset_button, self.cancel_button):
            button.setAutoDefault(False)
        self.apply_button.setDefault(True)
        actions.addWidget(self.reset_button)
        actions.addStretch()
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.apply_button)
        outer.addLayout(actions)
        self.cancel_button.clicked.connect(self.reject)
        self.apply_button.clicked.connect(self.apply)
        self.reset_button.clicked.connect(self._clear)
        self.select_button.clicked.connect(lambda: self._select_visible(True))
        self.deselect_button.clicked.connect(lambda: self._select_visible(False))
        self.search_edit.textChanged.connect(self._search)
        self.operator_combo.currentIndexChanged.connect(self._condition_changed)
        self.lower_edit.setText(self.initial_filter.first)
        self.upper_edit.setText(self.initial_filter.second)
        if self.kind is ColumnKind.BOOLEAN and self.initial_filter.operator not in OPERAND_FREE_OPERATORS:
            self.boolean_combo.setCurrentIndex(self.boolean_combo.findData(
                "true" if typed_value(self.initial_filter.first, self.kind) else "false"))
        self.operator_combo.setCurrentIndex(self.operator_combo.findData(self.initial_filter.operator.value))
        self._condition_changed()
        self.resize(self.theme.current.metric("filter_popup_width"), self.theme.current.metric("filter_popup_height"))

    def _branch(self, parent, text):
        item = QTreeWidgetItem(parent, [text])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
        item.setCheckState(0, Qt.Unchecked)
        return item

    def _leaf(self, parent, text, values, search):
        keys = frozenset(value.key for value in values)
        item = QTreeWidgetItem(parent, [text])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        selected = keys & self._initial_keys
        item.setCheckState(0, Qt.Checked if selected == keys else Qt.PartiallyChecked if selected else Qt.Unchecked)
        self._leaves.append((item, keys, search.casefold()))
        for key in keys:
            self.value_items[key] = item
        return item

    def _build_values(self):
        self.all_item = self._branch(self.values_tree, TEXT["all"])
        years, months, days = {}, {}, {}
        for value in self.values:
            if self.kind in (ColumnKind.DATE, ColumnKind.DATETIME) and isinstance(value.value, date):
                day = value.value.date() if isinstance(value.value, datetime) else value.value
                days.setdefault(day, []).append(value)
            else:
                self._leaf(self.all_item, value.label, [value], value.label)
        for day, values in sorted(days.items()):
            if day.year not in years:
                years[day.year] = self._branch(self.all_item, TEXT["year"].format(year=day.year))
            month_key = (day.year, day.month)
            if month_key not in months:
                months[month_key] = self._branch(years[day.year], TEXT["month"].format(month=day.month))
            search = " ".join([str(day.year), TEXT["month"].format(month=day.month), *(value.label for value in values)])
            self._leaf(months[month_key], TEXT["day"].format(day=day.day), values, search)
        self.all_item.setExpanded(True)
        for year in years.values():
            year.setExpanded(True)
        for month in months.values():
            month.setExpanded(True)
        if not self.values:
            self.all_item.setText(0, TEXT["empty"])
            self.all_item.setDisabled(True)

    def _condition_changed(self, *_):
        operator = FilterOperator(self.operator_combo.currentData())
        needs_operand = operator not in OPERAND_FREE_OPERATORS
        self.form.setRowVisible(self.lower_edit, needs_operand and self.kind is not ColumnKind.BOOLEAN)
        self.form.setRowVisible(self.upper_edit, operator is FilterOperator.BETWEEN)
        self.form.setRowVisible(self.boolean_combo, needs_operand and self.kind is ColumnKind.BOOLEAN)
        single, lower, upper = INPUT_LABELS[self.kind]
        label = OPERATOR_LABELS[operator] if operator is FilterOperator.REGEX else (
            lower if operator is FilterOperator.BETWEEN else single)
        self.form.labelForField(self.lower_edit).setText(label)
        self.form.labelForField(self.upper_edit).setText(upper)
        placeholder = TEXT["regex"] if operator is FilterOperator.REGEX else TEXT.get(self.kind.value, TEXT["text"])
        self.lower_edit.setPlaceholderText(placeholder)
        self.upper_edit.setPlaceholderText(placeholder)

    def _search(self, text):
        needle = text.casefold()
        for item, _, search in self._leaves:
            item.setHidden(bool(needle and needle not in search))

        def update_branch(item):
            if not item.childCount():
                return not item.isHidden()
            visible = any([update_branch(item.child(index)) for index in range(item.childCount())])
            item.setHidden(not visible)
            return visible

        update_branch(self.all_item)

    def _select_visible(self, selected):
        for item, _, _ in self._leaves:
            if not item.isHidden():
                item.setCheckState(0, Qt.Checked if selected else Qt.Unchecked)

    def selected_keys(self):
        keys = set()
        for item, values, _ in self._leaves:
            if item.checkState(0) == Qt.Checked:
                keys.update(values)
            elif item.checkState(0) == Qt.PartiallyChecked:
                keys.update(values & self._initial_keys)
        return None if keys == self._all_keys else frozenset(keys)

    def apply(self):
        first = self.boolean_combo.currentData() if self.kind is ColumnKind.BOOLEAN else self.lower_edit.text()
        specification = ColumnFilter(FilterOperator(self.operator_combo.currentData()), first,
                                     self.upper_edit.text(), self.selected_keys())
        try:
            self.proxy.apply_filter(self.column, specification)
        except ValueError as exc:
            self.error_label.setText(str(exc))
            self.error_label.show()
            self.scroll.ensureWidgetVisible(self.error_label)
            return
        self.accept()

    def _clear(self):
        self.proxy.clear_filter(self.column)
        self.accept()

    def _sort(self, order):
        self.proxy.sort(self.column, order)
        self.accept()

    def _unsort(self):
        self.proxy.sort(-1)
        self.accept()


class ResultTablePane(CdwWidget):
    """One tab's source data, filter state and view. Source row data never changes."""

    changed = Signal()

    def __init__(self, result: QueryResult, parent=None, *, theme=None):
        super().__init__(parent, theme=theme)
        self.source_model = ResultTableModel(self, theme=self.theme)
        self.source_model.set_result(result)
        self.proxy = ResultFilterProxyModel(self.source_model, self)
        self.view = CdwResultTableView(theme=self.theme)
        self.view.setObjectName("query_results")
        self.view.setModel(self.proxy)
        self.header = CdwFilterHeader(self.proxy, self.view, theme=self.theme)
        self.view.setHorizontalHeader(self.header)
        self.header.setSectionResizeMode(QHeaderView.Interactive)
        self.header.setStretchLastSection(True)
        self.header.filterRequested.connect(self.open_filter)
        self.view.filterRequested.connect(self.open_filter)
        self.header.sectionClicked.connect(self._sort_header)
        self.filter_popup = None
        layout = CdwVBoxLayout(self, role=LayoutRole.FLUSH, theme=self.theme)
        layout.addWidget(self.view)
        self.proxy.changed.connect(self.changed)
        self.view.resizeColumnsToContents()

    def row_indices(self) -> tuple[int, ...]:
        return self.proxy.row_indices()

    def current_result(self) -> QueryResult:
        return replace(self.source_model.result, rows=tuple(self.source_model.result.rows[index] for index in self.row_indices()))

    def _sort_header(self, column):
        order = (Qt.DescendingOrder if self.proxy.sortColumn() == column and self.proxy.sortOrder() == Qt.AscendingOrder
                 else Qt.AscendingOrder)
        self.proxy.sort(column, order)

    def open_filter(self, column: int):
        if not 0 <= column < self.source_model.columnCount():
            return None
        if self.filter_popup is not None:
            self.filter_popup.close()
            self.filter_popup.deleteLater()
        popup = ColumnFilterPopup(self.proxy, column, self, theme=self.theme)
        self.filter_popup = popup
        origin = self.header.mapToGlobal(QPoint(self.header.button_rect(column).left(), self.header.height()))
        available = self.screen().availableGeometry()
        popup.resize(min(popup.width(), available.width()), min(popup.height(), available.height()))
        origin.setX(max(available.left(), min(origin.x(), available.right() - popup.width() + 1)))
        origin.setY(max(available.top(), min(origin.y(), available.bottom() - popup.height() + 1)))
        popup.move(origin)
        popup.show()
        return popup
