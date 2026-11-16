"""Versioned persistence / CLI representation of Preset, shared by all clients."""

from __future__ import annotations

from typing import Any

from cdw.domain.contracts import (
    CdwError, ErrorCode, ExecutionPolicy, OutputFormat, Preset,
    StorageMode, StorageTarget, WriteMode,
)

PRESET_FORMAT_VERSION = 1
UNSUPPORTED_VERSION_MESSAGE = "지원하지 않는 프리셋 파일 버전입니다."
INVALID_ARGUMENTS_MESSAGE = "arguments는 문자열 목록이어야 합니다."


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
    }


def preset_from_dict(data: dict[str, Any]) -> Preset:
    try:
        if data["version"] != PRESET_FORMAT_VERSION:
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
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CdwError(ErrorCode.INVALID_PRESET, str(exc)) from exc
