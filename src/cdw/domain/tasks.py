"""Named local tasks and their table-file input contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from cdw.domain.contracts import (
    CdwError, ExecutionPolicy, OutputFormat, Preset, StorageMode, StorageTarget, WriteMode,
)
from cdw.domain.workspace import DEFAULT_EXPORT_DELIMITER, validate_delimiter

TASK_VERSION = 1
INPUT_PLACEHOLDER = "{input}"
TASK_COMMAND_TABLE = "task_input"
TASK_ENCODING = "utf-8"
INVALID_TASK = "작업 설정의 형식 또는 허용 범위를 확인해 주세요."
MISSING_INPUT = "인자에 {input}을 포함해 주세요. 실행 시 입력 테이블 파일의 경로로 바뀝니다."
INVALID_INPUT_FORMAT = "작업 입력 형식은 JSON 또는 CSV여야 합니다."
TASK_FIELDS = frozenset(("id", "name", "executable", "arguments", "working_directory", "environment",
                         "input_format", "csv_delimiter", "execution"))
EXECUTION_FIELDS = frozenset(("timeout_seconds", "max_output_bytes"))


class TaskInputFormat(StrEnum):
    JSON = "json"
    CSV = "csv"


@dataclass(frozen=True)
class TaskDefinition:
    id: str
    name: str
    executable: str
    arguments: tuple[str, ...]
    working_directory: str | None
    environment: Mapping[str, str]
    input_format: TaskInputFormat
    csv_delimiter: str
    execution: ExecutionPolicy

    def __post_init__(self):
        if not isinstance(self.input_format, TaskInputFormat):
            raise ValueError(INVALID_INPUT_FORMAT)
        validate_delimiter(self.csv_delimiter)
        # Command validation has one source: the same command contract used by
        # collection. Its output/storage fields are irrelevant to task input.
        try:
            command = self._command(self.arguments)
        except CdwError as exc:
            raise ValueError(exc.details or INVALID_TASK) from exc
        if not any(INPUT_PLACEHOLDER in argument for argument in command.arguments):
            raise ValueError(MISSING_INPUT)
        object.__setattr__(self, "name", command.name)
        object.__setattr__(self, "arguments", command.arguments)
        object.__setattr__(self, "environment", command.environment)

    def _command(self, arguments) -> Preset:
        return Preset(
            id=self.id, name=self.name, executable=self.executable, arguments=arguments,
            working_directory=self.working_directory, environment=self.environment,
            output_format=OutputFormat.JSON, encoding=TASK_ENCODING, csv_delimiter=DEFAULT_EXPORT_DELIMITER,
            storage=StorageTarget(StorageMode.MEMORY, None), table_name=TASK_COMMAND_TABLE,
            write_mode=WriteMode.REPLACE, execution=self.execution,
        )

    def command_for_input(self, path: str) -> Preset:
        return self._command(tuple(argument.replace(INPUT_PLACEHOLDER, path) for argument in self.arguments))

    def to_dict(self) -> dict:
        return {
            "version": TASK_VERSION, "id": self.id, "name": self.name, "executable": self.executable,
            "arguments": list(self.arguments), "working_directory": self.working_directory,
            "environment": dict(self.environment), "input_format": self.input_format.value,
            "csv_delimiter": self.csv_delimiter,
            "execution": {"timeout_seconds": self.execution.timeout_seconds,
                          "max_output_bytes": self.execution.max_output_bytes},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskDefinition":
        if (not isinstance(data, dict) or type(data.get("version")) is not int
                or data["version"] != TASK_VERSION or set(data) != TASK_FIELDS | {"version"}):
            raise ValueError(INVALID_TASK)
        execution = data["execution"]
        if not isinstance(execution, dict) or set(execution) != EXECUTION_FIELDS:
            raise ValueError(INVALID_TASK)
        values = {key: value for key, value in data.items() if key != "version"}
        values["input_format"] = TaskInputFormat(values["input_format"])
        values["execution"] = ExecutionPolicy(**execution)
        return cls(**values)


@dataclass(frozen=True)
class TaskRunResult:
    task_id: str
    task_name: str
    stdout: str
    stderr: str
    elapsed_seconds: float
    return_code: int
