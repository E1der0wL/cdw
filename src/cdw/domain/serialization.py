"""Versioned persistence / CLI representation of Preset, shared by all clients."""

from __future__ import annotations

from cdw.i18n import tr

from typing import Any, Mapping

from cdw.domain.contracts import (
    CdwError, CommandMode, ErrorCode, ExecutionPolicy, OutputFormat, Preset, QueryResult,
    StorageMode, StorageTarget, WriteMode,
)
from cdw.domain.parsing import ParseSpec

PRESET_FORMAT_VERSION = 2
UNSUPPORTED_VERSION_MESSAGE = tr('domain.serialization.unsupported_version_message')
INVALID_ARGUMENTS_MESSAGE = tr('domain.serialization.invalid_arguments_message')


def query_to_dict(result: QueryResult) -> dict[str, Any]:
    """CLI wire representation; presentation classification is internal metadata."""
    return {
        "columns": result.columns, "types": result.types, "rows": result.rows,
        "truncated": result.truncated, "elapsed_seconds": result.elapsed_seconds,
    }


def preset_to_dict(preset: Preset, *, include_environment: bool = True) -> dict[str, Any]:
    return {
        "version": PRESET_FORMAT_VERSION,
        "id": preset.id, "name": preset.name, "executable": preset.executable,
        "arguments": list(preset.arguments), "working_directory": preset.working_directory,
        "environment": dict(preset.environment) if include_environment else {},
        "output_format": preset.output_format.value, "encoding": preset.encoding,
        "csv_delimiter": preset.csv_delimiter,
        "storage": {"mode": preset.storage.mode.value, "path": preset.storage.path},
        "table_name": preset.table_name, "write_mode": preset.write_mode.value,
        "execution": {"timeout_seconds": preset.execution.timeout_seconds,
                      "max_output_bytes": preset.execution.max_output_bytes},
        "command_mode": preset.command_mode.value,
        "shell_script": preset.shell_script,
        "parsing": preset.parsing.to_dict(),
    }


def preset_from_dict(data: dict[str, Any]) -> Preset:
    try:
        if type(data["version"]) is not int or data["version"] != PRESET_FORMAT_VERSION:
            raise CdwError(ErrorCode.INVALID_PRESET, UNSUPPORTED_VERSION_MESSAGE)
        if not isinstance(data["arguments"], list):
            raise CdwError(ErrorCode.INVALID_PRESET, INVALID_ARGUMENTS_MESSAGE)
        return Preset(
            id=data["id"], name=data["name"], executable=data["executable"],
            arguments=tuple(data["arguments"]), working_directory=data["working_directory"],
            environment=data["environment"], output_format=OutputFormat(data["output_format"]),
            encoding=data["encoding"], csv_delimiter=data["csv_delimiter"],
            storage=StorageTarget(StorageMode(data["storage"]["mode"]), data["storage"]["path"]),
            table_name=data["table_name"], write_mode=WriteMode(data["write_mode"]),
            execution=ExecutionPolicy(**data["execution"]),
            command_mode=CommandMode(data["command_mode"]), shell_script=data["shell_script"],
            parsing=ParseSpec.from_dict(data["parsing"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CdwError(ErrorCode.INVALID_PRESET, str(exc)) from exc


def migrate_preset_document_v1(data: dict[str, Any], baseline_environment: Mapping[str, str]) -> dict[str, Any]:
    """One-time persistence migration; normal readers only accept the current schema."""
    try:
        if type(data.get("version")) is not int or data["version"] != 1:
            raise CdwError(ErrorCode.INVALID_PRESET, UNSUPPORTED_VERSION_MESSAGE)
        values = dict(data)
        values.update(version=PRESET_FORMAT_VERSION, command_mode=CommandMode.DIRECT.value,
                      shell_script="", parsing=ParseSpec().to_dict())
        values["environment"] = {**baseline_environment, **data["environment"]}
        # Validation and normalization are identical to all new documents.
        return preset_to_dict(preset_from_dict(values))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CdwError(ErrorCode.INVALID_PRESET, str(exc)) from exc
