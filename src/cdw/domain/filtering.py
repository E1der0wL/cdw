"""Typed, immutable column-filter contracts independent of Qt and table widgets."""

from __future__ import annotations

from cdw.i18n import tr

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import math
import re
from typing import Any, Callable

TRUE_VALUES = frozenset(("true", "1"))
FALSE_VALUES = frozenset(("false", "0"))
NULL_LABEL = "(NULL)"
EMPTY_LABEL = tr('domain.filtering.empty_label')
ERRORS = {
    "number": tr('domain.filtering.errors.number'),
    "date": tr('domain.filtering.errors.date'),
    "datetime": tr('domain.filtering.errors.datetime'),
    "time": tr('domain.filtering.errors.time'),
    "boolean": tr('domain.filtering.errors.boolean'),
    "range": tr('domain.filtering.errors.range'),
    "regex": tr('domain.filtering.errors.regex'),
    "operator": tr('domain.filtering.errors.operator'),
}

ValueKey = tuple[str, str]


class ColumnKind(StrEnum):
    NUMBER = "number"
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"
    BOOLEAN = "boolean"
    TEXT = "text"
    OTHER = "other"


class FilterOperator(StrEnum):
    ANY = "any"
    EQUAL = "equal"
    NOT_EQUAL = "not_equal"
    GREATER_EQUAL = "greater_equal"
    LESS_EQUAL = "less_equal"
    GREATER = "greater"
    LESS = "less"
    BETWEEN = "between"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    REGEX = "regex"
    IS_NULL = "is_null"
    NOT_NULL = "not_null"
    IS_EMPTY = "is_empty"


BASE_OPERATORS = (FilterOperator.ANY, FilterOperator.EQUAL, FilterOperator.NOT_EQUAL)
RANGE_OPERATORS = (FilterOperator.GREATER_EQUAL, FilterOperator.LESS_EQUAL, FilterOperator.GREATER,
                   FilterOperator.LESS, FilterOperator.BETWEEN)
TEXT_OPERATORS = (FilterOperator.CONTAINS, FilterOperator.STARTS_WITH, FilterOperator.ENDS_WITH,
                  FilterOperator.REGEX, FilterOperator.IS_EMPTY)
NULL_OPERATORS = (FilterOperator.IS_NULL, FilterOperator.NOT_NULL)
OPERAND_FREE_OPERATORS = frozenset((FilterOperator.ANY, *NULL_OPERATORS, FilterOperator.IS_EMPTY))
KIND_OPERATORS = {
    **dict.fromkeys((ColumnKind.NUMBER, ColumnKind.DATE, ColumnKind.DATETIME, ColumnKind.TIME),
                    BASE_OPERATORS + RANGE_OPERATORS + NULL_OPERATORS),
    ColumnKind.TEXT: BASE_OPERATORS + TEXT_OPERATORS + NULL_OPERATORS,
    ColumnKind.BOOLEAN: BASE_OPERATORS + NULL_OPERATORS,
    ColumnKind.OTHER: (FilterOperator.ANY,) + NULL_OPERATORS,
}


@dataclass(frozen=True)
class ColumnFilter:
    operator: FilterOperator = FilterOperator.ANY
    first: str = ""
    second: str = ""
    # None means every value, while an empty set intentionally selects no rows.
    selected_keys: frozenset[ValueKey] | None = None

    def __post_init__(self):
        if not isinstance(self.operator, FilterOperator) or not isinstance(self.first, str) or not isinstance(self.second, str):
            raise ValueError(ERRORS["operator"])
        if self.selected_keys is not None:
            object.__setattr__(self, "selected_keys", frozenset(self.selected_keys))

    @property
    def active(self) -> bool:
        return self.operator is not FilterOperator.ANY or self.selected_keys is not None


@dataclass(frozen=True)
class FilterValue:
    key: ValueKey
    value: Any
    label: str
    count: int


def operators_for(kind: ColumnKind) -> tuple[FilterOperator, ...]:
    return KIND_OPERATORS[kind]


def value_label(value: Any) -> str:
    if value is None:
        return NULL_LABEL
    if isinstance(value, str) and value == "":
        return EMPTY_LABEL
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def value_key(value: Any) -> ValueKey:
    if value is None:
        return ("null", "")
    if isinstance(value, bool):
        return ("boolean", str(value))
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        if not number.is_finite():
            return ("number", str(number))
        if number == 0:
            return ("number", "0")
        sign, digits, exponent = number.as_tuple()
        digits = list(digits)
        while digits and digits[-1] == 0:
            digits.pop()
            exponent += 1
        return ("number", f"{sign}:{''.join(map(str, digits))}:{exponent}")
    if isinstance(value, datetime):
        return ("datetime", value.isoformat())
    if isinstance(value, date):
        return ("date", value.isoformat())
    if isinstance(value, time):
        return ("time", value.isoformat())
    if isinstance(value, (bytes, bytearray, memoryview)):
        return ("bytes", bytes(value).hex())
    return (type(value).__name__, str(value))


def is_unordered(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or (isinstance(value, Decimal) and value.is_nan())


def _date_time(value: Any) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    # Treat a missing offset as local SQL wall time, consistently on every host.
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def typed_value(value: Any, kind: ColumnKind) -> Any:
    if kind is ColumnKind.NUMBER:
        return Decimal(str(value))
    if kind is ColumnKind.DATE:
        if isinstance(value, datetime):
            return value.date()
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    if kind is ColumnKind.DATETIME:
        return _date_time(value)
    if kind is ColumnKind.TIME:
        result = value if isinstance(value, time) else time.fromisoformat(str(value))
        # Match timestamp conditions: an omitted offset means SQL wall time at UTC.
        return (timedelta(hours=result.hour, minutes=result.minute, seconds=result.second,
                          microseconds=result.microsecond) - (result.utcoffset() or timedelta()))
    if kind is ColumnKind.BOOLEAN:
        if isinstance(value, bool):
            return value
        if str(value).strip().lower() in TRUE_VALUES:
            return True
        if str(value).strip().lower() in FALSE_VALUES:
            return False
        raise ValueError(ERRORS["boolean"])
    return str(value)


def sort_key(value: Any, kind: ColumnKind) -> tuple:
    if is_unordered(value):
        return (1, 0, "")
    try:
        typed = typed_value(value, kind)
        return (0, 0, typed.casefold() if isinstance(typed, str) else typed)
    except (ValueError, TypeError, InvalidOperation):
        return (0, 1, str(value).casefold())


def distinct_values(values, kind: ColumnKind) -> tuple[FilterValue, ...]:
    choices: dict[ValueKey, FilterValue] = {}
    for value in values:
        key = value_key(value)
        previous = choices.get(key)
        choices[key] = FilterValue(key, value, value_label(value), (previous.count if previous else 0) + 1)
    return tuple(sorted(choices.values(), key=lambda item: sort_key(item.value, kind)))


def _operand(text: str, kind: ColumnKind) -> Any:
    try:
        value = typed_value(text.strip() if kind not in (ColumnKind.TEXT, ColumnKind.OTHER) else text, kind)
        if kind is ColumnKind.NUMBER and not value.is_finite():
            raise ValueError(ERRORS["number"])
        return value
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise ValueError(ERRORS.get(kind.value, ERRORS["operator"])) from exc


def compile_filter(kind: ColumnKind, specification: ColumnFilter) -> Callable[[Any], bool]:
    """Validate before publishing a filter, then reuse its parsed operands."""
    operator = specification.operator
    if operator not in operators_for(kind):
        raise ValueError(ERRORS["operator"])
    expression = None
    if operator is FilterOperator.REGEX:
        try:
            expression = re.compile(specification.first)
        except re.error as exc:
            raise ValueError(ERRORS["regex"].format(details=str(exc))) from exc
    first = second = None
    if operator not in OPERAND_FREE_OPERATORS and operator is not FilterOperator.REGEX:
        first = _operand(specification.first, kind)
    if operator is FilterOperator.BETWEEN:
        second = _operand(specification.second, kind)
        if first > second:
            raise ValueError(ERRORS["range"])

    def matches(value: Any) -> bool:
        if specification.selected_keys is not None and value_key(value) not in specification.selected_keys:
            return False
        if operator is FilterOperator.ANY:
            return True
        if operator is FilterOperator.IS_NULL:
            return value is None
        if operator is FilterOperator.NOT_NULL:
            return value is not None
        if operator is FilterOperator.IS_EMPTY:
            return isinstance(value, str) and value == ""
        if value is None:
            return False
        if expression is not None:
            return expression.search(str(value)) is not None
        try:
            actual = typed_value(value, kind)
            if operator is FilterOperator.EQUAL:
                return actual == first
            if operator is FilterOperator.NOT_EQUAL:
                return actual != first
            if is_unordered(value):
                return False
            if operator is FilterOperator.GREATER_EQUAL:
                return actual >= first
            if operator is FilterOperator.LESS_EQUAL:
                return actual <= first
            if operator is FilterOperator.GREATER:
                return actual > first
            if operator is FilterOperator.LESS:
                return actual < first
            if operator is FilterOperator.BETWEEN:
                return first <= actual <= second
            text, part = str(actual).casefold(), str(first).casefold()
            if operator is FilterOperator.CONTAINS:
                return part in text
            if operator is FilterOperator.STARTS_WITH:
                return text.startswith(part)
            if operator is FilterOperator.ENDS_WITH:
                return text.endswith(part)
        except (ValueError, TypeError, InvalidOperation):
            return False
        return False

    return matches
