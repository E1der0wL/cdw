from __future__ import annotations

from cdw.i18n import tr

import json
import sqlite3
from cdw.infrastructure.metadata_database import MetadataDatabase

from cdw.domain.contracts import CdwError, ErrorCode, Preset, RunRecord, RunState
from cdw.domain.serialization import preset_from_dict, preset_to_dict
from cdw.domain.appearance import AppearanceSettings

class MetadataRepository:

    APPEARANCE_KEY = "appearance"
    GET_SETTING_SQL = "SELECT definition FROM app_settings WHERE key=?"
    SAVE_SETTING_SQL = "INSERT INTO app_settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET definition=excluded.definition"
    INVALID_APPEARANCE_MESSAGE = tr('infrastructure.metadata.invalid_appearance_message')
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

    def __init__(self, database: MetadataDatabase):
        self.database = database

    def save_preset(self, preset: Preset) -> None:
        with self.database.connect() as connection:
            try:
                connection.execute(MetadataRepository.UPSERT_PRESET_SQL, (
                    preset.id, preset.name, preset.storage.key, preset.table_name,
                    json.dumps(preset_to_dict(preset), ensure_ascii=False),
                ))
            except sqlite3.IntegrityError as exc:
                raise CdwError(ErrorCode.PRESET_CONFLICT) from exc

    def load_appearance(self) -> AppearanceSettings:
        with self.database.connect() as connection:
            row = connection.execute(MetadataRepository.GET_SETTING_SQL, (MetadataRepository.APPEARANCE_KEY,)).fetchone()
        if row is None:
            return AppearanceSettings()
        try:
            return AppearanceSettings.from_dict(json.loads(row[0]))
        except (ValueError, TypeError, KeyError) as exc:
            raise CdwError(ErrorCode.STORAGE_FAILED, MetadataRepository.INVALID_APPEARANCE_MESSAGE) from exc

    def save_appearance(self, settings: AppearanceSettings) -> None:
        with self.database.connect() as connection:
            connection.execute(MetadataRepository.SAVE_SETTING_SQL, (MetadataRepository.APPEARANCE_KEY, json.dumps(settings.to_dict(), ensure_ascii=False)))

    def list_presets(self) -> list[Preset]:
        with self.database.connect() as connection:
            return [preset_from_dict(json.loads(row[0])) for row in connection.execute(MetadataRepository.PRESET_LIST_SQL)]

    def get_preset(self, preset_id: str) -> Preset:
        with self.database.connect() as connection:
            row = connection.execute(MetadataRepository.PRESET_GET_SQL, (preset_id,)).fetchone()
        if row is None:
            raise CdwError(ErrorCode.NOT_FOUND, preset_id)
        return preset_from_dict(json.loads(row[0]))

    def start_run(self, run_id: str, preset: Preset, timestamp: str) -> None:
        with self.database.connect() as connection:
            connection.execute(MetadataRepository.INSERT_RUN_SQL, (
                run_id, preset.id, preset.name, RunState.RUNNING.value, timestamp,
                preset.storage.key, preset.table_name,
                json.dumps(preset_to_dict(preset, include_environment=False), ensure_ascii=False),
            ))

    def set_stage(self, run_id: str, state: RunState) -> None:
        with self.database.connect() as connection:
            connection.execute(MetadataRepository.UPDATE_STAGE_SQL, (state.value, run_id))

    def finish_run(self, run_id: str, state: RunState, timestamp: str, *, rows: int | None,
                   elapsed: float | None, error_code: str | None = None,
                   details: str = "", stderr: str = "") -> None:
        with self.database.connect() as connection:
            connection.execute(MetadataRepository.FINISH_RUN_SQL, (
                state.value, timestamp, rows, elapsed, error_code, details, stderr, run_id,
            ))

    def list_runs(self, limit: int) -> list[RunRecord]:
        with self.database.connect() as connection:
            return [self._run(row) for row in connection.execute(MetadataRepository.HISTORY_SQL, (limit,))]

    def pending_runs(self) -> list[RunRecord]:
        with self.database.connect() as connection:
            return [self._run(row) for row in connection.execute(MetadataRepository.PENDING_SQL, (
                RunState.RUNNING.value, RunState.PARSING.value, RunState.SAVING.value,
            ))]

    def last_success(self, preset_id: str) -> RunRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(MetadataRepository.LAST_SUCCESS_SQL, (preset_id, RunState.SUCCEEDED.value)).fetchone()
        return self._run(row) if row else None

    @staticmethod
    def _run(row: sqlite3.Row) -> RunRecord:
        fields = dict(row)
        fields.pop("preset_snapshot")
        fields["state"] = RunState(fields["state"])
        return RunRecord(**fields)
