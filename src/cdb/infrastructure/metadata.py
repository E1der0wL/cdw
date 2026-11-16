from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from cdb.domain.contracts import CdbError, ErrorCode, Preset, RunRecord, RunState
from cdb.domain.serialization import preset_from_dict, preset_to_dict
from cdb.domain.appearance import AppearanceSettings

SQLITE_TIMEOUT_SECONDS = 10
SCHEMA_VERSION = 2
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS presets (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, storage_key TEXT NOT NULL,
    table_name TEXT NOT NULL, definition TEXT NOT NULL,
    UNIQUE(storage_key, table_name)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, preset_id TEXT NOT NULL, preset_name TEXT NOT NULL,
    state TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    row_count INTEGER, elapsed_seconds REAL, error_code TEXT,
    details TEXT NOT NULL DEFAULT '', stderr TEXT NOT NULL DEFAULT '',
    storage_key TEXT NOT NULL, table_name TEXT NOT NULL, preset_snapshot TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_started ON runs(started_at DESC);
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, definition TEXT NOT NULL);
"""
APPEARANCE_KEY = "appearance"
GET_SETTING_SQL = "SELECT definition FROM app_settings WHERE key=?"
SAVE_SETTING_SQL = "INSERT INTO app_settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET definition=excluded.definition"
INVALID_APPEARANCE_MESSAGE = "저장된 화면 설정을 읽지 못했습니다. 설정에서 기본값을 적용해 복구할 수 있습니다."
UPSERT_PRESET_SQL = """INSERT INTO presets VALUES (?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET name=excluded.name, storage_key=excluded.storage_key,
table_name=excluded.table_name, definition=excluded.definition"""
PRESET_LIST_SQL = "SELECT definition FROM presets ORDER BY name COLLATE NOCASE"
PRESET_GET_SQL = "SELECT definition FROM presets WHERE id=?"
INSERT_RUN_SQL = """INSERT INTO runs
(id,preset_id,preset_name,state,started_at,storage_key,table_name,preset_snapshot)
VALUES (?,?,?,?,?,?,?,?)"""
UPDATE_STAGE_SQL = "UPDATE runs SET state=? WHERE id=?"
FINISH_RUN_SQL = """UPDATE runs SET state=?,finished_at=?,row_count=?,elapsed_seconds=?,
error_code=?,details=?,stderr=? WHERE id=?"""
HISTORY_SQL = "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?"
PENDING_SQL = "SELECT * FROM runs WHERE state IN (?, ?, ?)"
LAST_SUCCESS_SQL = "SELECT * FROM runs WHERE preset_id=? AND state=? ORDER BY started_at DESC LIMIT 1"
VERSION_SQL = "PRAGMA user_version"
SET_VERSION_SQL = f"PRAGMA user_version={SCHEMA_VERSION}"
UNSUPPORTED_SCHEMA_MESSAGE = "이 버전보다 새로운 메타데이터 형식입니다."


class MetadataRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            version = connection.execute(VERSION_SQL).fetchone()[0]
            if version > SCHEMA_VERSION:
                raise CdbError(ErrorCode.STORAGE_FAILED, UNSUPPORTED_SCHEMA_MESSAGE)
            connection.executescript(SCHEMA_SQL)
            connection.execute(SET_VERSION_SQL)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=SQLITE_TIMEOUT_SECONDS)
            connection.row_factory = sqlite3.Row
            with connection:
                yield connection
        except sqlite3.Error as exc:
            raise CdbError(ErrorCode.STORAGE_FAILED, str(exc)) from exc
        finally:
            if connection is not None:
                connection.close()

    def save_preset(self, preset: Preset) -> None:
        with self._connect() as connection:
            try:
                connection.execute(UPSERT_PRESET_SQL, (
                    preset.id, preset.name, preset.storage.key, preset.table_name,
                    json.dumps(preset_to_dict(preset), ensure_ascii=False),
                ))
            except sqlite3.IntegrityError as exc:
                raise CdbError(ErrorCode.PRESET_CONFLICT) from exc

    def load_appearance(self) -> AppearanceSettings:
        with self._connect() as connection:
            row = connection.execute(GET_SETTING_SQL, (APPEARANCE_KEY,)).fetchone()
        if row is None:
            return AppearanceSettings()
        try:
            return AppearanceSettings.from_dict(json.loads(row[0]))
        except (ValueError, TypeError, KeyError) as exc:
            raise CdbError(ErrorCode.STORAGE_FAILED, INVALID_APPEARANCE_MESSAGE) from exc

    def save_appearance(self, settings: AppearanceSettings) -> None:
        with self._connect() as connection:
            connection.execute(SAVE_SETTING_SQL, (APPEARANCE_KEY, json.dumps(settings.to_dict(), ensure_ascii=False)))

    def list_presets(self) -> list[Preset]:
        with self._connect() as connection:
            return [preset_from_dict(json.loads(row[0])) for row in connection.execute(PRESET_LIST_SQL)]

    def get_preset(self, preset_id: str) -> Preset:
        with self._connect() as connection:
            row = connection.execute(PRESET_GET_SQL, (preset_id,)).fetchone()
        if row is None:
            raise CdbError(ErrorCode.NOT_FOUND, preset_id)
        return preset_from_dict(json.loads(row[0]))

    def start_run(self, run_id: str, preset: Preset, timestamp: str) -> None:
        with self._connect() as connection:
            connection.execute(INSERT_RUN_SQL, (
                run_id, preset.id, preset.name, RunState.RUNNING.value, timestamp,
                preset.storage.key, preset.table_name,
                json.dumps(preset_to_dict(preset, include_environment=False), ensure_ascii=False),
            ))

    def set_stage(self, run_id: str, state: RunState) -> None:
        with self._connect() as connection:
            connection.execute(UPDATE_STAGE_SQL, (state.value, run_id))

    def finish_run(self, run_id: str, state: RunState, timestamp: str, *, rows: int | None,
                   elapsed: float | None, error_code: str | None = None,
                   details: str = "", stderr: str = "") -> None:
        with self._connect() as connection:
            connection.execute(FINISH_RUN_SQL, (
                state.value, timestamp, rows, elapsed, error_code, details, stderr, run_id,
            ))

    def list_runs(self, limit: int) -> list[RunRecord]:
        with self._connect() as connection:
            return [self._run(row) for row in connection.execute(HISTORY_SQL, (limit,))]

    def pending_runs(self) -> list[RunRecord]:
        with self._connect() as connection:
            return [self._run(row) for row in connection.execute(PENDING_SQL, (
                RunState.RUNNING.value, RunState.PARSING.value, RunState.SAVING.value,
            ))]

    def last_success(self, preset_id: str) -> RunRecord | None:
        with self._connect() as connection:
            row = connection.execute(LAST_SUCCESS_SQL, (preset_id, RunState.SUCCEEDED.value)).fetchone()
        return self._run(row) if row else None

    @staticmethod
    def _run(row: sqlite3.Row) -> RunRecord:
        fields = dict(row)
        fields.pop("preset_snapshot")
        fields["state"] = RunState(fields["state"])
        return RunRecord(**fields)
