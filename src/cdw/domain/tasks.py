"""External command tasks and the versioned table-file input contract."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
import shlex
from typing import Mapping

from cdw.domain.contracts import (
    CdwError, CommandMode, CommandSpec, ExecutionPolicy, validate_definition_identity,
)
from cdw.domain.workspace import validate_delimiter
from cdw.i18n import tr

INPUT_PLACEHOLDER = "{input}"
TASK_ENCODING = "utf-8"


class TaskInputFormat(StrEnum):
    NONE = "none"
    JSON = "json"
    CSV = "csv"


@dataclass(frozen=True)
class TaskInputContext:
    """Identity of the immutable table snapshot handed to an external process."""

    INVALID_CONTEXT = tr("tasks.protocol.invalid_context")

    run_id: str
    table_name: str
    collected_at: str

    def __post_init__(self):
        if (not isinstance(self.run_id, str) or not self.run_id
                or not isinstance(self.table_name, str) or not isinstance(self.collected_at, str)):
            raise ValueError(self.INVALID_CONTEXT)
        try:
            timestamp = datetime.fromisoformat(self.collected_at)
        except ValueError as exc:
            raise ValueError(self.INVALID_CONTEXT) from exc
        if timestamp.tzinfo is None:
            raise ValueError(self.INVALID_CONTEXT)


@dataclass(frozen=True)
class TaskDefinition:
    TASK_VERSION = 2
    PREVIOUS_VERSION = 1
    INVALID_TASK = tr("domain.tasks.invalid_task")
    MISSING_INPUT = tr("tasks.validation.missing_input")
    UNEXPECTED_INPUT = tr("tasks.validation.unexpected_input")
    QUOTED_SHELL_INPUT = tr("tasks.validation.shell_input")
    INVALID_INPUT_FORMAT = tr("tasks.validation.input_format")
    V1_FIELDS = frozenset(("id", "name", "executable", "arguments", "working_directory", "environment",
                           "input_format", "csv_delimiter", "execution"))
    TASK_FIELDS = V1_FIELDS | {"command_mode", "script"}
    EXECUTION_FIELDS = frozenset(("timeout_seconds", "max_output_bytes"))

    id: str
    name: str
    executable: str
    arguments: tuple[str, ...]
    working_directory: str | None
    environment: Mapping[str, str]
    input_format: TaskInputFormat
    csv_delimiter: str
    execution: ExecutionPolicy
    command_mode: CommandMode = CommandMode.DIRECT
    script: str = ""

    def __post_init__(self):
        if not isinstance(self.input_format, TaskInputFormat):
            raise ValueError(self.INVALID_INPUT_FORMAT)
        if self.input_format is TaskInputFormat.CSV:
            validate_delimiter(self.csv_delimiter)
        elif not isinstance(self.csv_delimiter, str):
            raise ValueError(self.INVALID_INPUT_FORMAT)
        try:
            name = validate_definition_identity(self.id, self.name)
            command = self.command
        except CdwError as exc:
            raise ValueError(exc.details or self.INVALID_TASK) from exc
        has_input = (INPUT_PLACEHOLDER in command.script if command.mode is CommandMode.SHELL
                     else any(INPUT_PLACEHOLDER in argument for argument in command.arguments))
        if self.input_format is TaskInputFormat.NONE and has_input:
            raise ValueError(self.UNEXPECTED_INPUT)
        if self.input_format is not TaskInputFormat.NONE and not has_input:
            raise ValueError(self.MISSING_INPUT)
        if has_input and command.mode is CommandMode.SHELL:
            self._validate_shell_placeholder(command.script)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "arguments", command.arguments)
        object.__setattr__(self, "environment", command.environment)

    @property
    def command(self) -> CommandSpec:
        return CommandSpec(self.executable, self.arguments, self.working_directory,
                           self.environment, self.execution, TASK_ENCODING,
                           mode=self.command_mode, script=self.script)

    @property
    def requires_input(self) -> bool:
        return self.input_format is not TaskInputFormat.NONE

    def command_for_input(self, path: str) -> CommandSpec:
        if not self.requires_input:
            raise ValueError(self.UNEXPECTED_INPUT)
        command = self.command
        if command.mode is CommandMode.SHELL:
            # In shell scripts the placeholder is an unquoted shell word.
            # The adapter supplies quoting; the path is never executable syntax.
            return replace(command, script=command.script.replace(INPUT_PLACEHOLDER, shlex.quote(path)))
        return replace(command, arguments=tuple(argument.replace(INPUT_PLACEHOLDER, path)
                                                for argument in command.arguments))

    @classmethod
    def _validate_shell_placeholder(cls, script: str):
        quote = None
        escaped = False
        for index, character in enumerate(script):
            if script.startswith(INPUT_PLACEHOLDER, index) and (quote is not None or escaped):
                raise ValueError(cls.QUOTED_SHELL_INPUT)
            if escaped:
                escaped = False
            elif character == "\\" and quote != "'":
                escaped = True
            elif character in ("'", '"'):
                if quote is None:
                    quote = character
                elif quote == character:
                    quote = None

    def to_dict(self) -> dict:
        return {
            "version": self.TASK_VERSION, "id": self.id, "name": self.name, "executable": self.executable,
            "arguments": list(self.arguments), "working_directory": self.working_directory,
            "environment": dict(self.environment), "input_format": self.input_format.value,
            "csv_delimiter": self.csv_delimiter, "command_mode": self.command_mode.value, "script": self.script,
            "execution": {"timeout_seconds": self.execution.timeout_seconds,
                          "max_output_bytes": self.execution.max_output_bytes},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskDefinition":
        if (not isinstance(data, dict) or type(data.get("version")) is not int
                or data["version"] != cls.TASK_VERSION or set(data) != cls.TASK_FIELDS | {"version"}):
            raise ValueError(cls.INVALID_TASK)
        execution = data["execution"]
        if not isinstance(execution, dict) or set(execution) != cls.EXECUTION_FIELDS:
            raise ValueError(cls.INVALID_TASK)
        values = {key: value for key, value in data.items() if key != "version"}
        values["input_format"] = TaskInputFormat(values["input_format"])
        values["command_mode"] = CommandMode(values["command_mode"])
        values["execution"] = ExecutionPolicy(**execution)
        return cls(**values)


def migrate_task_document_v1(data: dict, baseline_environment: Mapping[str, str]) -> dict:
    """One-time migration called by the database upgrade, never by the reader."""
    if (not isinstance(data, dict) or type(data.get("version")) is not int
            or data["version"] != TaskDefinition.PREVIOUS_VERSION
            or set(data) != TaskDefinition.V1_FIELDS | {"version"}
            or not isinstance(data.get("environment"), dict)):
        raise ValueError(TaskDefinition.INVALID_TASK)
    environment = dict(baseline_environment)
    environment.update(data["environment"])
    upgraded = dict(data, version=TaskDefinition.TASK_VERSION, environment=environment,
                    command_mode=CommandMode.DIRECT.value, script="")
    return TaskDefinition.from_dict(upgraded).to_dict()


@dataclass(frozen=True)
class TaskRunResult:
    task_id: str
    task_name: str
    stdout: str
    stderr: str
    elapsed_seconds: float
    return_code: int
