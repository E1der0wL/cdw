"""The single definition of the application's public data and error contracts."""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from cdw.branding import APP_NAME

TABLE_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MEMORY_STORAGE_KEY = ":memory:"
SOURCE_SCHEMA = "source"
VALIDATION_MESSAGES = {
    "execution": "실행 시간과 출력 크기 제한은 양수여야 합니다. 출력 크기는 정수 바이트입니다.",
    "preview": "preview_rows must be a positive integer",
    "storage_type": "저장 위치의 형식이 올바르지 않습니다.",
    "absolute_database": "DB 파일은 절대 경로로 지정해 주세요.",
    "memory_path": "메모리 저장에는 파일 경로를 지정하지 않습니다.",
    "strings": "프리셋의 문자열 필드를 확인해 주세요.",
    "modes": "출력 형식 또는 저장 방식을 확인해 주세요.",
    "policies": "저장 위치 또는 실행 제한을 확인해 주세요.",
    "containers": "인자는 목록, 환경변수는 객체여야 합니다.",
    "directory_type": "작업 디렉터리 형식을 확인해 주세요.",
    "required": "이름과 실행 파일을 입력해 주세요.",
    "table": "테이블 이름: 영문 소문자·숫자·밑줄, 최대 63자. 숫자로 시작할 수 없습니다.",
    "delimiter": "CSV 구분자는 한 바이트 문자여야 합니다.",
    "encoding": "알 수 없는 인코딩: {encoding}",
    "absolute_directory": "작업 디렉터리는 절대 경로로 지정해 주세요.",
    "arguments": "인자는 문자열이어야 하며 NUL 문자를 포함할 수 없습니다.",
    "environment": "환경변수 이름 또는 값을 확인해 주세요.",
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


ERROR_MESSAGES = {
    ErrorCode.INVALID_PRESET: "프리셋 설정을 확인해 주세요.",
    ErrorCode.PRESET_CONFLICT: "같은 이름 또는 같은 저장 대상의 프리셋이 있습니다.",
    ErrorCode.NOT_FOUND: "대상을 찾을 수 없습니다.",
    ErrorCode.COMMAND_FAILED: "명령어 실행에 실패했습니다.",
    ErrorCode.TIMEOUT: "명령어 실행 시간이 제한을 초과했습니다.",
    ErrorCode.CANCELLED: "실행을 취소했습니다.",
    ErrorCode.OUTPUT_LIMIT: "명령어 출력 크기가 제한을 초과했습니다.",
    ErrorCode.PARSE_FAILED: "명령어 출력을 표로 변환하지 못했습니다.",
    ErrorCode.EMPTY_OUTPUT: "빈 데이터는 저장하지 않습니다. 기존 데이터는 유지됩니다.",
    ErrorCode.SCHEMA_MISMATCH: "추가할 데이터의 컬럼 또는 자료형이 기존 테이블과 다릅니다.",
    ErrorCode.STORAGE_FAILED: "데이터 저장에 실패했습니다.",
    ErrorCode.INVALID_QUERY: "SQL 조회는 하나의 SELECT 문만 지원합니다.",
    ErrorCode.QUERY_FAILED: "SQL 조회에 실패했습니다.",
    ErrorCode.PROFILE_BUSY: f"다른 {APP_NAME} 프로세스에서 이 작업 환경을 사용하고 있습니다.",
    ErrorCode.INTERNAL: "처리 중 오류가 발생했습니다.",
}


class CdwError(Exception):
    def __init__(self, code: ErrorCode, details: str = "") -> None:
        self.code = code
        self.details = details
        super().__init__(ERROR_MESSAGES[code])


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

    def __post_init__(self) -> None:
        strings = (self.id, self.name, self.executable, self.encoding, self.csv_delimiter, self.table_name)
        if any(not isinstance(value, str) or "\0" in value for value in strings):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["strings"])
        if not isinstance(self.output_format, OutputFormat) or not isinstance(self.write_mode, WriteMode):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["modes"])
        if not isinstance(self.storage, StorageTarget) or not isinstance(self.execution, ExecutionPolicy):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["policies"])
        if not isinstance(self.arguments, (tuple, list)) or not isinstance(self.environment, Mapping):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["containers"])
        if self.working_directory is not None and (not isinstance(self.working_directory, str) or "\0" in self.working_directory):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["directory_type"])
        if not self.id or not self.name.strip() or not self.executable.strip():
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["required"])
        if not TABLE_NAME_PATTERN.fullmatch(self.table_name):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["table"])
        if len(self.csv_delimiter.encode("utf-8")) != 1 or self.csv_delimiter in "\r\n":
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["delimiter"])
        try:
            codecs.lookup(self.encoding)
        except LookupError as exc:
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["encoding"].format(encoding=self.encoding)) from exc
        if self.working_directory and not Path(self.working_directory).is_absolute():
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["absolute_directory"])
        if any(not isinstance(arg, str) or "\0" in arg for arg in self.arguments):
            raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["arguments"])
        for key, value in self.environment.items():
            if not isinstance(key, str) or not ENVIRONMENT_NAME_PATTERN.fullmatch(key) or not isinstance(value, str) or "\0" in value:
                raise CdwError(ErrorCode.INVALID_PRESET, VALIDATION_MESSAGES["environment"])
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "arguments", tuple(self.arguments))
        object.__setattr__(self, "environment", MappingProxyType(dict(self.environment)))

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


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    types: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool
    elapsed_seconds: float


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
