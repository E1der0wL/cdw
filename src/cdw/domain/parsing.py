"""Output-shape policy independent of Polars and any particular command."""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from cdw.i18n import tr


class ColumnCountMode(StrEnum):
    DYNAMIC = "dynamic"
    FIXED = "fixed"


class DelimiterMode(StrEnum):
    CHARACTER = "character"
    WHITESPACE = "whitespace"


def validate_column_names(names: tuple[str, ...]) -> tuple[str, ...]:
    if any(not isinstance(name, str) or not name.strip() or "\0" in name for name in names):
        raise ValueError(tr("collection.parse.invalid_names"))
    cleaned = tuple(name.strip() for name in names)
    if len({name.casefold() for name in cleaned}) != len(cleaned):
        raise ValueError(tr("collection.parse.duplicate_names"))
    return cleaned


def validate_csv_delimiter(value: str) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) != 1 or value in '\r\n\0"':
        raise ValueError(tr("collection.parse.invalid_delimiter"))
    return value


@dataclass(frozen=True)
class ParseSpec:
    GENERATED_COLUMN: ClassVar[str] = "column_{index}"

    has_header: bool = True
    column_mode: ColumnCountMode = ColumnCountMode.DYNAMIC
    column_count: int | None = None
    column_names: tuple[str, ...] = ()
    delimiter_mode: DelimiterMode = DelimiterMode.CHARACTER
    infer_types: bool = True

    def __post_init__(self):
        if type(self.has_header) is not bool or type(self.infer_types) is not bool:
            raise ValueError(tr("collection.parse.invalid_flags"))
        if not isinstance(self.column_mode, ColumnCountMode) or not isinstance(self.delimiter_mode, DelimiterMode):
            raise ValueError(tr("collection.parse.invalid_mode"))
        if not isinstance(self.column_names, (tuple, list)):
            raise ValueError(tr("collection.parse.invalid_names"))
        names = validate_column_names(tuple(self.column_names))
        if self.column_mode is ColumnCountMode.FIXED:
            if type(self.column_count) is not int or self.column_count < 1:
                raise ValueError(tr("collection.parse.invalid_count"))
            if names and len(names) != self.column_count:
                raise ValueError(tr("collection.parse.name_count"))
        elif self.column_count is not None or names:
            raise ValueError(tr("collection.parse.dynamic_names"))
        object.__setattr__(self, "column_names", names)

    @classmethod
    def generated_names(cls, count: int) -> tuple[str, ...]:
        return tuple(cls.GENERATED_COLUMN.format(index=index + 1) for index in range(count))

    def resolve_column_names(self, width: int, header: list[str] | None) -> tuple[str, ...]:
        if self.column_mode is ColumnCountMode.FIXED:
            return self.column_names or self.generated_names(width)
        return validate_column_names(tuple(header)) if header is not None else self.generated_names(width)

    def to_dict(self) -> dict:
        return {"has_header": self.has_header, "column_mode": self.column_mode.value,
                "column_count": self.column_count, "column_names": list(self.column_names),
                "delimiter_mode": self.delimiter_mode.value, "infer_types": self.infer_types}

    @classmethod
    def from_dict(cls, data: dict) -> "ParseSpec":
        return cls(has_header=data["has_header"], column_mode=ColumnCountMode(data["column_mode"]),
                   column_count=data["column_count"], column_names=data["column_names"],
                   delimiter_mode=DelimiterMode(data["delimiter_mode"]), infer_types=data["infer_types"])
