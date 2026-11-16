"""Immutable query-tab and accessible-table contracts shared by UI and service."""

from dataclasses import dataclass
from enum import StrEnum

from cdb.domain.contracts import QueryResult, StorageMode

DEFAULT_EXPORT_DELIMITER = ","
INVALID_DELIMITER = "구분자는 줄바꿈·따옴표·NUL을 제외한 문자 하나로 지정해 주세요."


def validate_delimiter(delimiter: str) -> str:
    if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in '\r\n\0"':
        raise ValueError(INVALID_DELIMITER)
    return delimiter


class ExportFormat(StrEnum):
    CSV = "csv"
    JSON = "json"
    DUCKDB = "duckdb"


class CopyFormat(StrEnum):
    TEXT = "text"
    HTML = "html"


@dataclass(frozen=True)
class QuerySnapshot:
    id: str
    title: str
    result: QueryResult
    created_at: str


@dataclass(frozen=True)
class WorkspaceTable:
    name: str
    storage_mode: StorageMode
    updated_at: str | None
    columns: tuple[tuple[str, str], ...]
    storage_key: str
    preset_id: str | None = None
    display_name: str = ""

    def __post_init__(self) -> None:
        # The storage adapter knows the unquoted leaf name. Older callers may
        # omit it; keep their full SQL name visible instead of a blank label.
        if not self.display_name:
            object.__setattr__(self, "display_name", self.name)
