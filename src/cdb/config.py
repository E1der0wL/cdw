"""Policy selection and defaults. Executors receive these values explicitly."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from cdb.domain.contracts import ExecutionPolicy, QueryPolicy

QT_BINDING = "pyside6"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_OUTPUT_MIB = 16
BYTES_PER_MIB = 1024 * 1024
DEFAULT_PREVIEW_ROWS = 500
DEFAULT_ENCODING = "utf-8"
DEFAULT_CSV_DELIMITER = ","
DEFAULT_DATABASE_NAME = "data.duckdb"
METADATA_DATABASE_NAME = "metadata.sqlite"
PROFILE_LOCK_NAME = "session.lock"
DEFAULT_HISTORY_LIMIT = 200
ENV_DATA_DIRECTORY = "CDW_DATA_DIR"
LEGACY_ENV_DATA_DIRECTORY = "CDB_DATA_DIR"
DATA_DIRECTORY_NAME = "cdb"


@dataclass(frozen=True)
class AppConfig:
    data_directory: Path
    execution_policy: ExecutionPolicy
    query_policy: QueryPolicy

    @property
    def default_database(self) -> Path:
        return self.data_directory / DEFAULT_DATABASE_NAME

    @property
    def metadata_database(self) -> Path:
        return self.data_directory / METADATA_DATABASE_NAME


def default_data_directory() -> Path:
    override = os.environ.get(ENV_DATA_DIRECTORY) or os.environ.get(LEGACY_ENV_DATA_DIRECTORY)
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / DATA_DIRECTORY_NAME


def build_config(data_directory: Path | None = None) -> AppConfig:
    return AppConfig(
        data_directory=(data_directory or default_data_directory()).resolve(),
        execution_policy=ExecutionPolicy(DEFAULT_TIMEOUT_SECONDS, DEFAULT_OUTPUT_MIB * BYTES_PER_MIB),
        query_policy=QueryPolicy(DEFAULT_PREVIEW_ROWS),
    )
