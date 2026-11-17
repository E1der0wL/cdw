"""Immutable query-tab and accessible-table contracts shared by UI and service."""

from cdw.i18n import tr

from dataclasses import dataclass
from enum import StrEnum

from cdw.domain.contracts import QueryResult, StorageMode

DEFAULT_EXPORT_DELIMITER = ","
INVALID_DELIMITER = tr('domain.workspace.invalid_delimiter')


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
    display_name: str
    preset_id: str | None = None
