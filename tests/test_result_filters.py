"""Public filtering contracts and actual header/popup interactions."""

from datetime import date, datetime, time, timezone
from decimal import Decimal
import os
from pathlib import Path

import pytest
from qtpy.QtCore import Qt
from qtpy.QtGui import QFontDatabase, QFontMetrics
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication

from cdw.domain.contracts import QueryResult
from cdw.application.service import ApplicationService
from cdw.config import build_config
from cdw.domain.filtering import (
    ColumnFilter, ColumnKind, FilterOperator, compile_filter, distinct_values, value_key,
)
from cdw.ui.design.manager import ThemeManager
from cdw.ui.models import MAX_CELL_CHARACTERS, RAW_VALUE_ROLE
from cdw.ui.result_table import ResultTablePane


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    if os.name == "nt" and os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        for filename in ("malgun.ttf", "malgunbd.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / filename))
    return app


@pytest.fixture
def pane_factory(application):
    manager = ThemeManager()
    manager.activate()
    panes = []

    def create(rows, types=("INTEGER", "VARCHAR"), columns=None):
        result = QueryResult(columns or tuple(f"column_{index}" for index in range(len(types))),
                             types, tuple(tuple(row) for row in rows), False, 0.125)
        pane = ResultTablePane(result, theme=manager)
        pane.resize(800, 480)
        pane.show()
        panes.append(pane)
        application.processEvents()
        return pane

    yield create
    for pane in panes:
        pane.close()
        pane.deleteLater()
    application.processEvents()


@pytest.mark.parametrize("operator,first,second,expected", [
    (FilterOperator.GREATER_EQUAL, "2", "", [2, 3, 10]),
    (FilterOperator.LESS_EQUAL, "2", "", [1, 2]),
    (FilterOperator.GREATER, "2", "", [3, 10]),
    (FilterOperator.LESS, "2", "", [1]),
    (FilterOperator.BETWEEN, "2", "3", [2, 3]),
])
def test_numeric_ranges_are_typed_and_inclusive_where_requested(operator, first, second, expected):
    predicate = compile_filter(ColumnKind.NUMBER, ColumnFilter(operator, first, second))
    assert [value for value in (None, 1, 2, 3, 10) if predicate(value)] == expected


def test_precise_numbers_are_not_rounded_or_collapsed():
    first = Decimal("123456789012345678901234567890.1234567")
    second = Decimal("123456789012345678901234567890.1234568")
    predicate = compile_filter(ColumnKind.NUMBER, ColumnFilter(FilterOperator.GREATER, str(first)))
    assert not predicate(first) and predicate(second)
    assert value_key(first) != value_key(second)
    assert value_key(Decimal("2.00")) == value_key(2)
    assert value_key(Decimal("-0.00")) == value_key(0)


def test_duplicates_null_and_blank_are_separate_choices():
    choices = distinct_values((None, "", "NULL", "red", "red"), ColumnKind.TEXT)
    assert len(choices) == 4
    assert next(choice for choice in choices if choice.value == "red").count == 2
    predicate = compile_filter(ColumnKind.TEXT, ColumnFilter(selected_keys=frozenset((value_key(None), value_key("")))))
    assert predicate(None) and predicate("")
    assert not predicate("NULL") and not predicate("red")
    assert not compile_filter(ColumnKind.TEXT, ColumnFilter(selected_keys=frozenset()))("")


def test_calendar_and_timezone_ranges():
    predicate = compile_filter(ColumnKind.DATE, ColumnFilter(FilterOperator.BETWEEN, "2026-02-01", "2026-03-01"))
    assert predicate(date(2026, 2, 1)) and predicate(date(2026, 3, 1))
    assert not predicate(date(2026, 1, 31))
    instant = compile_filter(ColumnKind.DATETIME, ColumnFilter(FilterOperator.EQUAL, "2026-01-01T09:00:00+09:00"))
    assert instant(datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert instant(datetime(2026, 1, 1))


@pytest.mark.parametrize("kind,specification", [
    (ColumnKind.NUMBER, ColumnFilter(FilterOperator.GREATER, "letters")),
    (ColumnKind.NUMBER, ColumnFilter(FilterOperator.LESS, "NaN")),
    (ColumnKind.NUMBER, ColumnFilter(FilterOperator.BETWEEN, "3", "2")),
    (ColumnKind.DATE, ColumnFilter(FilterOperator.EQUAL, "2026-02-30")),
    (ColumnKind.TIME, ColumnFilter(FilterOperator.EQUAL, "25:00:00")),
    (ColumnKind.TIME, ColumnFilter(FilterOperator.BETWEEN, "18:00", "09:00")),
    (ColumnKind.NUMBER, ColumnFilter(FilterOperator.REGEX, "^2")),
    (ColumnKind.TEXT, ColumnFilter(FilterOperator.BETWEEN, "a", "z")),
    (ColumnKind.TEXT, ColumnFilter(FilterOperator.REGEX, "[")),
])
def test_invalid_conditions_are_rejected(kind, specification):
    with pytest.raises(ValueError):
        compile_filter(kind, specification)


def test_numeric_sort_null_last_ties_stable_and_clear_restores_source(pane_factory):
    pane = pane_factory([(10, "a"), (2, "b"), (None, "c"), (2, "d")])
    pane.proxy.sort(0, Qt.AscendingOrder)
    assert pane.row_indices() == (1, 3, 0, 2)
    pane.proxy.sort(0, Qt.DescendingOrder)
    assert pane.row_indices() == (0, 1, 3, 2)
    pane.proxy.sort(-1)
    assert pane.row_indices() == (0, 1, 2, 3)


def test_filters_intersect_and_failed_update_keeps_last_result(pane_factory):
    pane = pane_factory([(1, "apple"), (2, "Apricot"), (3, "banana"), (4, "apple"), (5, None)])
    pane.proxy.apply_filter(0, ColumnFilter(FilterOperator.BETWEEN, "2", "4"))
    pane.proxy.apply_filter(1, ColumnFilter(FilterOperator.REGEX, "(?i)^a"))
    assert pane.row_indices() == (1, 3)
    with pytest.raises(ValueError):
        pane.proxy.apply_filter(1, ColumnFilter(FilterOperator.REGEX, "["))
    assert pane.row_indices() == (1, 3)
    pane.proxy.apply_filter(1, ColumnFilter(FilterOperator.CONTAINS, "APP", selected_keys=frozenset((value_key("apple"),))))
    assert pane.row_indices() == (3,)
    pane.proxy.clear_filter(0)
    assert pane.row_indices() == (0, 3)


def test_independent_tabs_and_raw_snapshot_values(pane_factory):
    long_text = "x" * (MAX_CELL_CHARACTERS + 100)
    rows = [(2, long_text), (1, "short")]
    first, second = pane_factory(rows), pane_factory(rows)
    first.proxy.sort(0, Qt.AscendingOrder)
    first.proxy.apply_filter(0, ColumnFilter(FilterOperator.GREATER_EQUAL, "2"))
    assert first.row_indices() == (0,) and second.row_indices() == (0, 1)
    result = first.current_result()
    assert result.rows == ((2, long_text),)
    assert result.columns == first.source_model.result.columns
    assert result.types == ("INTEGER", "VARCHAR")
    assert first.source_model.result.rows == tuple(rows)
    assert first.source_model.data(first.source_model.index(0, 1), RAW_VALUE_ROLE) == long_text
    assert first.source_model.data(first.source_model.index(0, 1)) != long_text


def test_header_button_opens_popup_and_apply_is_explicit(application, pane_factory):
    pane = pane_factory([(1, "one"), (2, "two"), (10, "ten")])
    QTest.mouseClick(pane.header.viewport(), Qt.LeftButton, pos=pane.header.button_rect(0).center())
    application.processEvents()
    popup = pane.filter_popup
    assert popup is not None and popup.isVisible()
    assert popup.windowFlags() & Qt.NoDropShadowWindowHint
    popup.operator_combo.setCurrentIndex(popup.operator_combo.findData(FilterOperator.BETWEEN.value))
    popup.lower_edit.setText("2")
    popup.upper_edit.setText("10")
    assert pane.row_indices() == (0, 1, 2)
    QTest.mouseClick(popup.apply_button, Qt.LeftButton)
    assert pane.row_indices() == (1, 2) and not popup.isVisible()


def test_initial_column_width_keeps_full_header_and_sort_marker_visible(application, pane_factory):
    pane = pane_factory([(42.5, "healthy")], types=("DOUBLE", "VARCHAR"), columns=("cpu_percent", "status"))
    metrics = QFontMetrics(pane.header.font())
    initial_width = pane.header.sectionSize(0)
    for order, caption in ((Qt.AscendingOrder, "cpu_percent ↑"), (Qt.DescendingOrder, "cpu_percent ↓")):
        pane.proxy.sort(0, order)
        application.processEvents()
        available = pane.header.text_rect(0)
        assert pane.header.sectionSize(0) == initial_width
        assert metrics.elidedText(caption, Qt.ElideRight, available.width()) == caption
        assert available.right() < pane.header.button_rect(0).left()


def test_popup_validation_leaves_active_filter_unchanged(application, pane_factory):
    pane = pane_factory([(1, "one"), (2, "two")])
    pane.proxy.apply_filter(1, ColumnFilter(FilterOperator.EQUAL, "one"))
    popup = pane.open_filter(1)
    popup.operator_combo.setCurrentIndex(popup.operator_combo.findData(FilterOperator.REGEX.value))
    popup.lower_edit.setText("[")
    QTest.mouseClick(popup.apply_button, Qt.LeftButton)
    application.processEvents()
    assert popup.isVisible() and popup.error_label.isVisible()
    assert pane.row_indices() == (0,)
    assert pane.proxy.filter_for(1).operator is FilterOperator.EQUAL


def test_enter_in_condition_field_applies_filter_instead_of_sorting(application, pane_factory):
    pane = pane_factory([(10, "ten"), (2, "two"), (1, "one")])
    popup = pane.open_filter(0)
    popup.operator_combo.setCurrentIndex(popup.operator_combo.findData(FilterOperator.GREATER.value))
    popup.lower_edit.setText("2")
    popup.lower_edit.setFocus()
    application.processEvents()
    QTest.keyClick(popup.lower_edit, Qt.Key_Return)
    assert pane.row_indices() == (0,)
    assert pane.proxy.sortColumn() == -1
    assert not popup.isVisible()


def test_search_selection_and_cancel_do_not_change_existing_filter(pane_factory):
    pane = pane_factory([(1, "apple"), (2, "banana"), (3, "apple")])
    popup = pane.open_filter(1)
    popup.search_edit.setText("app")
    assert popup.value_items[value_key("banana")].isHidden()
    QTest.mouseClick(popup.deselect_button, Qt.LeftButton)
    popup.search_edit.clear()
    assert not popup.value_items[value_key("banana")].isHidden()
    assert popup.value_items[value_key("apple")].checkState(0) == Qt.Unchecked
    assert pane.row_indices() == (0, 1, 2)
    QTest.mouseClick(popup.cancel_button, Qt.LeftButton)
    assert pane.row_indices() == (0, 1, 2)
    popup = pane.open_filter(1)
    popup.value_items[value_key("banana")].setCheckState(0, Qt.Unchecked)
    QTest.mouseClick(popup.apply_button, Qt.LeftButton)
    assert pane.row_indices() == (0, 2)


def test_date_tree_year_month_day_selection_and_timestamps(pane_factory):
    values = [datetime(2025, 12, 31, 9), datetime(2026, 1, 2, 9), datetime(2026, 1, 2, 18), datetime(2026, 2, 1), None]
    pane = pane_factory([(value,) for value in values], types=("TIMESTAMP",))
    popup = pane.open_filter(0)
    day = popup.value_items[value_key(values[1])]
    assert day is popup.value_items[value_key(values[2])]
    month, year = day.parent(), day.parent().parent()
    assert (day.text(0), month.text(0), year.text(0)) == ("02일", "01월", "2026년")
    popup.all_item.setCheckState(0, Qt.Unchecked)
    month.setCheckState(0, Qt.Checked)
    assert year.checkState(0) == Qt.PartiallyChecked
    QTest.mouseClick(popup.apply_button, Qt.LeftButton)
    assert pane.row_indices() == (1, 2)
    popup = pane.open_filter(0)
    assert popup.value_items[value_key(values[1])].checkState(0) == Qt.Checked
    year = popup.value_items[value_key(values[1])].parent().parent()
    year.setCheckState(0, Qt.Checked)
    QTest.mouseClick(popup.apply_button, Qt.LeftButton)
    assert pane.row_indices() == (1, 2, 3)


def test_empty_result_keeps_schema_and_keyboard_sort_controls(application, pane_factory):
    empty = pane_factory([], types=("DATE",))
    assert empty.current_result().types == ("DATE",) and empty.row_indices() == ()
    popup = empty.open_filter(0)
    assert popup.all_item.isDisabled()
    QTest.mouseClick(popup.apply_button, Qt.LeftButton)
    pane = pane_factory([(2, "b"), (1, "a")])
    pane.view.setCurrentIndex(pane.proxy.index(0, 0))
    QTest.keyClick(pane.view, Qt.Key_Down, Qt.AltModifier)
    application.processEvents()
    QTest.mouseClick(pane.filter_popup.ascending_button, Qt.LeftButton)
    assert pane.row_indices() == (1, 0)
    popup = pane.open_filter(0)
    QTest.mouseClick(popup.unsort_button, Qt.LeftButton)
    assert pane.row_indices() == (0, 1)


@pytest.mark.parametrize("expression,kind,label", [
    ("42::DECIMAL(12, 2)", ColumnKind.NUMBER, "숫자"),
    ("DATE '2026-11-01'", ColumnKind.DATE, "날짜"),
    ("TIMESTAMP '2026-11-01 09:30:00'", ColumnKind.DATETIME, "날짜·시각"),
    ("TIMESTAMPTZ '2026-11-01 09:30:00+09:00'", ColumnKind.DATETIME, "날짜·시각"),
    ("TIME '09:30:00'", ColumnKind.TIME, "시각"),
    ("TIMETZ '09:30:00+09:00'", ColumnKind.TIME, "시각"),
    ("'2026-11-01'::VARCHAR", ColumnKind.TEXT, "문자"),
    ("true", ColumnKind.BOOLEAN, "논리값"),
    ("[TIMESTAMP '2026-11-01 09:30:00']", ColumnKind.OTHER, "값 선택"),
    ("[42::DECIMAL(12, 2)]", ColumnKind.OTHER, "값 선택"),
])
def test_filter_controls_follow_actual_sql_column_types(tmp_path, pane_factory, expression, kind, label):
    service = ApplicationService(build_config(tmp_path))
    try:
        result = service.query_workspace(None, f"SELECT {expression} AS sample").result
    finally:
        service.close()
    pane = pane_factory(result.rows, result.types, result.columns)
    popup = pane.open_filter(0)
    assert pane.proxy.kind(0) is kind
    assert f"{label} 필터" in popup.windowTitle()
    assert result.types[0] in popup.type_label.text()
    operators = {popup.operator_combo.itemData(i) for i in range(popup.operator_combo.count())}
    assert {"any", "is_null", "not_null"} <= operators
    if kind in (ColumnKind.NUMBER, ColumnKind.DATE, ColumnKind.DATETIME, ColumnKind.TIME):
        assert {"equal", "not_equal", "greater", "greater_equal", "less", "less_equal", "between"} <= operators
        assert not {"regex", "contains", "starts_with", "ends_with", "is_empty"} & operators
        popup.operator_combo.setCurrentIndex(popup.operator_combo.findData("between"))
        assert popup.lower_edit.isVisible() and popup.upper_edit.isVisible()
        assert not popup.boolean_combo.isVisible()
    elif kind is ColumnKind.TEXT:
        assert {"equal", "not_equal", "contains", "starts_with", "ends_with", "regex", "is_empty"} <= operators
        assert not {"greater", "less", "between"} & operators
        # Date-looking VARCHAR data remains a flat text checklist.
        assert popup.all_item.child(0).text(0) == "2026-11-01"
    elif kind is ColumnKind.BOOLEAN:
        assert operators == {"any", "equal", "not_equal", "is_null", "not_null"}
        popup.operator_combo.setCurrentIndex(popup.operator_combo.findData("equal"))
        assert popup.boolean_combo.isVisible() and not popup.lower_edit.isVisible()
    else:
        assert operators == {"any", "is_null", "not_null"}
        assert not popup.lower_edit.isVisible() and not popup.upper_edit.isVisible()


def test_time_range_sort_and_invalid_condition_preserve_result(pane_factory):
    values = [time(18), time(9, 30), None, time(9, 29, 59, 999999), time(10)]
    pane = pane_factory([(value,) for value in values], types=("TIME",))
    popup = pane.open_filter(0)
    popup.operator_combo.setCurrentIndex(popup.operator_combo.findData("between"))
    popup.lower_edit.setText("09:30")
    popup.upper_edit.setText("10:00")
    QTest.mouseClick(popup.apply_button, Qt.LeftButton)
    assert pane.row_indices() == (1, 4)
    popup = pane.open_filter(0)
    popup.lower_edit.setText("not a time")
    QTest.mouseClick(popup.apply_button, Qt.LeftButton)
    assert popup.isVisible() and popup.error_label.isVisible()
    assert pane.row_indices() == (1, 4)
    popup.reject()
    pane.proxy.clear_filter(0)
    pane.proxy.sort(0, Qt.AscendingOrder)
    assert pane.row_indices() == (3, 1, 4, 0, 2)
    instant = compile_filter(ColumnKind.TIME, ColumnFilter(FilterOperator.EQUAL, "09:30:00.123456+09:00"))
    assert instant(time(0, 30, 0, 123456, tzinfo=timezone.utc))
    assert not instant(time(0, 30, 0, 123455, tzinfo=timezone.utc))


def test_boolean_choice_apply_and_reopen(pane_factory):
    pane = pane_factory([(True,), (False,), (None,), (False,)], types=("BOOLEAN",))
    popup = pane.open_filter(0)
    popup.operator_combo.setCurrentIndex(popup.operator_combo.findData("equal"))
    popup.boolean_combo.setCurrentIndex(popup.boolean_combo.findData("false"))
    QTest.mouseClick(popup.apply_button, Qt.LeftButton)
    assert pane.row_indices() == (1, 3)
    popup = pane.open_filter(0)
    assert popup.boolean_combo.currentData() == "false"
    popup.operator_combo.setCurrentIndex(popup.operator_combo.findData("is_null"))
    assert not popup.boolean_combo.isVisible()
    QTest.mouseClick(popup.apply_button, Qt.LeftButton)
    assert pane.row_indices() == (2,)
