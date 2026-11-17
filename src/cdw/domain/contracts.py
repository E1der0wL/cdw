"""The single definition of the application's public data and error contracts."""

from __future__ import annotations

from cdw.i18n import tr

import codecs
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from cdw.branding import APP_NAME
from cdw.domain.filtering import ColumnKind
from cdw.domain.parsing import ParseSpec, validate_csv_delimiter

TABLE_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
MEMORY_STORAGE_KEY = ":memory:"
SOURCE_SCHEMA = "source"
VALIDATION_MESSAGES = {
    "execution": tr('domain.contracts.validation_messages.execution'),
    "preview": "preview_rows must be a positive integer",
    "storage_type": tr('domain.contracts.validation_messages.storage_type'),
    "absolute_database": tr('domain.contracts.validation_messages.absolute_database'),
    "memory_path": tr('domain.contracts.validation_messages.memory_path'),
    "strings": tr('domain.contracts.validation_messages.strings'),
    "modes": tr('domain.contracts.validation_messages.modes'),
    "policies": tr('domain.contracts.validation_messages.policies'),
    "containers": tr('domain.contracts.validation_messages.containers'),
    "directory_type": tr('domain.contracts.validation_messages.directory_type'),
    "required": tr('domain.contracts.validation_messages.required'),
    "table": tr('domain.contracts.validation_messages.table'),
    "delimiter": tr('domain.contracts.validation_messages.delimiter'),
    "encoding": tr('domain.contracts.validation_messages.encoding'),
    "absolute_directory": tr('domain.contracts.validation_messages.absolute_directory'),
    "arguments": tr('domain.contracts.validation_messages.arguments'),
    "environment": tr('domain.contracts.validation_messages.environment'),
}


class StorageMode(StrEnum):
    FILE = "file"
    MEMORY = "memory"


class OutputFormat(StrEnum):
    JSON = "json"
    NDJSON = "ndjson"
    CSV = "csv"
    TEXT = "text"


class WriteMode(StrEnum):
    REPLACE = "replace"
    APPEND = "append"


class CommandMode(StrEnum):
    DIRECT = "direct"
    SHELL = "shell"


class RunState(StrEnum):
    RUNNING = "running"
    PARSING = "parsing"
    SAVING = "saving"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ErrorCode(StrEnum):
    INVALID_PRESET = "invalid_preset"
    PRESET_CONFLICT = "preset_conflict"
    NOT_FOUND = "not_found"
    COMMAND_FAILED = "command_failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    OUTPUT_LIMIT = "output_limit"
    PARSE_FAILED = "parse_failed"
    EMPTY_OUTPUT = "empty_output"
    SCHEMA_MISMATCH = "schema_mismatch"
    STORAGE_FAILED = "storage_failed"
    INVALID_QUERY = "invalid_query"
    QUERY_FAILED = "query_failed"
    PROFILE_BUSY = "profile_busy"
    INTERNAL = "internal"


class CdwError(Exception):

    ERROR_MESSAGES = {
        ErrorCode.INVALID_PRESET: tr('domain.contracts.error_messages'),
        ErrorCode.PRESET_CONFLICT: tr('domain.contracts.error_messages_2'),
        ErrorCode.NOT_FOUND: tr('domain.contracts.error_messages_3'),
        ErrorCode.COMMAND_FAILED: tr('domain.contracts.error_messages_4'),
        ErrorCode.TIMEOUT: tr('domain.contracts.error_messages_5'),
        ErrorCode.CANCELLED: tr('domain.contracts.error_messages_6'),
        ErrorCode.OUTPUT_LIMIT: tr('domain.contracts.error_messages_7'),
        ErrorCode.PARSE_FAILED: tr('domain.contracts.error_messages_8'),
        ErrorCode.EMPTY_OUTPUT: tr('domain.contracts.error_messages_9'),
        ErrorCode.SCHEMA_MISMATCH: tr('domain.contracts.error_messages_10'),
        ErrorCode.STORAGE_FAILED: tr('domain.contracts.error_messages_11'),
        ErrorCode.INVALID_QUERY: tr('domain.contracts.error_messages_12'),
        ErrorCode.QUERY_FAILED: tr('domain.contracts.error_messages_13'),
        ErrorCode.PROFILE_BUSY: tr('domain.contracts.error_messages_14').format(app_name=APP_NAME),
        ErrorCode.INTERNAL: tr('domain.contracts.error_messages_15'),
    }

    def __init__(self, code: ErrorCode, details: str = "") -> None:
        self.code = code
        self.details = details
        super().__init__(CdwError.ERROR_MESSAGES[code])


@dataclass(frozen=True)
class ExecutionPolicy:
    timeout_seconds: float
    max_output_bytes: int

    def __post_init__(self) -> None:
        if (not isinstance(self.timeout_seconds, (int, float)) or isinstance(self.timeout_seconds, bool)
                or not 0 < self.timeout_seconds < float("inf")
                or not isinstance(self.max_output_bytes, int) or isinstance(self.max_output_bytes, bool)
                or self.max_output_bytes <= 0):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["execution"])


@dataclass(frozen=True)
class QueryPolicy:
    preview_rows: int

    def __post_init__(self) -> None:
        if not isinstance(self.preview_rows, int) or isinstance(self.preview_rows, bool) or self.preview_rows <= 0:
            raise ValueError(VALIDATION_MESSAGES["preview"])


@dataclass(frozen=True)
class StorageTarget:
    mode: StorageMode
    path: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, StorageMode) or (self.path is not None and not isinstance(self.path, str)):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["storage_type"])
        if self.mode is StorageMode.FILE:
            if not self.path or not Path(self.path).is_absolute():
                raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["absolute_database"])
            object.__setattr__(self, "path", str(Path(self.path).resolve()))
        elif self.path is not None:
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["memory_path"])

    @property
    def key(self) -> str:
        return self.path if self.mode is StorageMode.FILE else MEMORY_STORAGE_KEY  # type: ignore[return-value]


@dataclass(frozen=True)
class CommandSpec:
    """Process input, independent of collection, storage and task input formats."""
    executable: str
    arguments: tuple[str, ...]
    working_directory: str | None
    environment: Mapping[str, str]
    execution: ExecutionPolicy
    encoding: str
    mode: CommandMode = CommandMode.DIRECT
    script: str = ""

    def __post_init__(self):
        if any(not isinstance(value, str) or "\0" in value for value in (self.executable, self.encoding)):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["strings"])
        if not self.executable.strip():
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["required"])
        if not isinstance(self.mode, CommandMode) or not isinstance(self.script, str) or "\0" in self.script:
            raise CdwError(ErrorCode.INVALID_PRESET, tr("collection.command.invalid_mode"))
        if self.mode is CommandMode.SHELL:
            if not self.script.strip() or self.arguments:
                raise CdwError(ErrorCode.INVALID_PRESET, tr("collection.command.invalid_script"))
        elif self.script:
            raise CdwError(ErrorCode.INVALID_PRESET, tr("collection.command.direct_script"))
        if not isinstance(self.execution, ExecutionPolicy):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["policies"])
        if not isinstance(self.arguments, (tuple, list)) or not isinstance(self.environment, Mapping):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["containers"])
        if self.working_directory is not None and (not isinstance(self.working_directory, str) or "\0" in self.working_directory):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["directory_type"])
        if self.working_directory and not Path(self.working_directory).is_absolute():
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["absolute_directory"])
        if any(not isinstance(arg, str) or "\0" in arg for arg in self.arguments):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["arguments"])
        for key, value in self.environment.items():
            if (not isinstance(key, str) or not key or "=" in key or "\0" in key
                    or not isinstance(value, str) or "\0" in value):
                raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["environment"])
        try:
            codecs.lookup(self.encoding)
        except LookupError as exc:
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["encoding"].format(encoding=self.encoding)) from exc
        object.__setattr__(self, "arguments", tuple(self.arguments))
        object.__setattr__(self, "environment", MappingProxyType(dict(self.environment)))


def validate_definition_identity(identifier: str, name: str) -> str:
    if any(not isinstance(value, str) or "\0" in value for value in (identifier, name)):
        raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["strings"])
    if not identifier or not name.strip():
        raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["required"])
    return name.strip()


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    executable: str
    arguments: tuple[str, ...]
    working_directory: str | None
    environment: Mapping[str, str]
    output_format: OutputFormat
    encoding: str
    csv_delimiter: str
    storage: StorageTarget
    table_name: str
    write_mode: WriteMode
    execution: ExecutionPolicy
    command_mode: CommandMode = CommandMode.DIRECT
    shell_script: str = ""
    parsing: ParseSpec = field(default_factory=ParseSpec)

    def __post_init__(self):
        name = validate_definition_identity(self.id, self.name)
        command = self.command
        if any(not isinstance(value, str) or "\0" in value for value in (self.csv_delimiter, self.table_name)):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["strings"])
        if not isinstance(self.output_format, OutputFormat) or not isinstance(self.write_mode, WriteMode):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["modes"])
        if not isinstance(self.storage, StorageTarget):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["storage_type"])
        if not TABLE_NAME_PATTERN.fullmatch(self.table_name):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["table"])
        if not isinstance(self.parsing, ParseSpec):
            raise CdwError(ErrorCode.INVALID_PRESET, tr("collection.parse.invalid_spec"))
        try:
            validate_csv_delimiter(self.csv_delimiter)
        except ValueError as exc:
            raise CdwError(ErrorCode.INVALID_PRESET, str(exc)) from exc
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "arguments", command.arguments)
        object.__setattr__(self, "environment", command.environment)

    @property
    def command(self) -> CommandSpec:
        # A derived immutable view; preset fields remain the only stored source.
        return CommandSpec(self.executable, self.arguments, self.working_directory,
                           self.environment, self.execution, self.encoding, self.command_mode, self.shell_script)

    @property
    def qualified_table(self) -> str:
        return f"{SOURCE_SCHEMA}.{self.table_name}"


@dataclass(frozen=True)
class CommandOutput:
    stdout: bytes
    stderr: bytes
    return_code: int


@dataclass(frozen=True)
class Progress:
    run_id: str
    state: RunState
    message: str


@dataclass(frozen=True)
class CollectionResult:
    run_id: str
    inserted_rows: int
    total_rows: int
    elapsed_seconds: float
    stderr: str
    finished_at: str


@dataclass(frozen=True)
class CollectionPreview:
    stdout: str
    stderr: str
    columns: tuple[str, ...]
    types: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    total_rows: int
    elapsed_seconds: float
    parse_error: str = ""


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    types: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool
    elapsed_seconds: float
    kinds: tuple[ColumnKind, ...]

    def __post_init__(self):
        if len(self.columns) != len(self.types) or len(self.columns) != len(self.kinds):
            raise ValueError(tr('domain.contracts.queryresult.post_init'))
        if any(not isinstance(kind, ColumnKind) for kind in self.kinds):
            raise ValueError(tr('domain.contracts.queryresult.post_init_2'))


@dataclass(frozen=True)
class TableInfo:
    name: str
    columns: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RunRecord:
    id: str
    preset_id: str
    preset_name: str
    state: RunState
    started_at: str
    finished_at: str | None
    row_count: int | None
    elapsed_seconds: float | None
    error_code: str | None
    details: str
    stderr: str
    storage_key: str
    table_name: str
