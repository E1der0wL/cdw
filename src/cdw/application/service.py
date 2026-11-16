from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from cdw.config import AppConfig, DEFAULT_HISTORY_LIMIT
from cdw.domain.contracts import (
    CdwError, CollectionResult, ErrorCode, MEMORY_STORAGE_KEY, Preset, Progress,
    QueryResult, RunRecord, RunState, StorageMode, StorageTarget, TableInfo,
)
from cdw.infrastructure.duckdb_store import DuckDBStore
from cdw.infrastructure.metadata import MetadataRepository
from cdw.infrastructure.parsers import OutputParser
from cdw.infrastructure.runner import SubprocessRunner
from cdw.domain.appearance import AppearanceSettings
from cdw.domain.workspace import DEFAULT_EXPORT_DELIMITER, QuerySnapshot, WorkspaceTable

STAGE_MESSAGES = {
    RunState.RUNNING: "명령어를 실행하고 있습니다.",
    RunState.PARSING: "출력을 표로 변환하고 있습니다.",
    RunState.SAVING: "DuckDB에 저장하고 있습니다.",
}
RECOVERED_MESSAGE = "이전 실행의 DB 저장 완료 기록을 확인해 복구했습니다."
INTERRUPTED_MESSAGE = "이전 프로세스가 실행 완료 전에 종료되었습니다."


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApplicationService:
    """Public synchronous API. Long operations run on one caller-owned worker.

    UI and CLI share these operations and their contracts. Metadata reads are
    short-lived connections; DuckDB calls must stay on one thread.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.metadata = MetadataRepository(config.metadata_database)
        self._runner = SubprocessRunner()
        self._parser = OutputParser()
        self._store = DuckDBStore()

    def save_preset(self, preset: Preset) -> Preset:
        self.metadata.save_preset(preset)
        return preset

    def load_appearance(self) -> AppearanceSettings:
        return self.metadata.load_appearance()

    def save_appearance(self, settings: AppearanceSettings) -> None:
        self.metadata.save_appearance(settings)

    def list_presets(self) -> list[Preset]:
        return self.metadata.list_presets()

    def get_preset(self, preset_id: str) -> Preset:
        return self.metadata.get_preset(preset_id)

    def list_runs(self, limit: int = DEFAULT_HISTORY_LIMIT) -> list[RunRecord]:
        return self.metadata.list_runs(limit)

    def last_success(self, preset_id: str) -> RunRecord | None:
        return self.metadata.last_success(preset_id)

    def recover(self) -> None:
        for run in self.metadata.pending_runs():
            target = StorageTarget(StorageMode.MEMORY, None) if run.storage_key == MEMORY_STORAGE_KEY else StorageTarget(StorageMode.FILE, run.storage_key)
            commit = self._store.committed(target, run.id) if target.mode is StorageMode.FILE else None
            self.metadata.finish_run(
                run.id, RunState.SUCCEEDED if commit else RunState.INTERRUPTED, utc_now(),
                rows=commit[0] if commit else None, elapsed=None,
                details=RECOVERED_MESSAGE if commit else INTERRUPTED_MESSAGE,
            )

    def collect(self, preset_id: str, cancel: threading.Event | None = None,
                on_progress: Callable[[Progress], None] | None = None) -> CollectionResult:
        preset = self.get_preset(preset_id)
        cancellation = cancel if cancel is not None else threading.Event()
        run_id = str(uuid4())
        started = time.monotonic()
        stderr = ""
        self.metadata.start_run(run_id, preset, utc_now())

        def stage(state: RunState) -> None:
            if cancellation.is_set():
                raise CdwError(ErrorCode.CANCELLED)
            self.metadata.set_stage(run_id, state)
            if on_progress:
                on_progress(Progress(run_id, state, STAGE_MESSAGES[state]))

        try:
            stage(RunState.RUNNING)
            output = self._runner.run(preset, cancellation)
            stderr = output.stderr.decode(preset.encoding, errors="replace")
            stage(RunState.PARSING)
            frame = self._parser.parse(output.stdout, preset)
            stage(RunState.SAVING)
            inserted, total = self._store.write(preset, frame, run_id)
            elapsed = time.monotonic() - started
            # Keep SAVING if metadata completion fails: recover() reconciles the
            # authoritative DuckDB commit instead of rerunning an append.
        except CdwError as exc:
            state = RunState.CANCELLED if exc.code is ErrorCode.CANCELLED else RunState.FAILED
            self.metadata.finish_run(run_id, state, utc_now(), rows=None,
                                     elapsed=time.monotonic() - started,
                                     error_code=exc.code.value, details=str(exc) + "\n" + exc.details,
                                     stderr=stderr)
            raise
        except Exception as exc:
            self.metadata.finish_run(run_id, RunState.FAILED, utc_now(), rows=None,
                                     elapsed=time.monotonic() - started,
                                     error_code=ErrorCode.INTERNAL.value, details=str(exc), stderr=stderr)
            raise CdwError(ErrorCode.INTERNAL, str(exc)) from exc
        self.metadata.finish_run(run_id, RunState.SUCCEEDED, utc_now(), rows=inserted,
                                 elapsed=elapsed, stderr=stderr)
        return CollectionResult(run_id, inserted, total, elapsed, stderr)

    def query(self, preset_id: str, sql: str, cancel: threading.Event | None = None) -> QueryResult:
        return self._store.query(self.get_preset(preset_id).storage, sql, self.config.query_policy, cancel)

    def list_tables(self, preset_id: str) -> list[TableInfo]:
        return self._store.tables(self.get_preset(preset_id).storage)

    def query_workspace(self, preset_id: str | None, sql: str, cancel: threading.Event | None = None) -> QuerySnapshot:
        target = self.get_preset(preset_id).storage if preset_id else None
        return self._store.query_workspace(target, sql, self.list_presets(), cancel)

    def workspace_tables(self) -> list[WorkspaceTable]:
        presets = self.list_presets()
        updates = {}
        for preset in presets:
            latest = self.last_success(preset.id)
            if latest and latest.finished_at:
                key = (preset.storage.key, preset.table_name)
                updates[key] = max(updates.get(key, latest.finished_at), latest.finished_at)
        return self._store.workspace_tables(presets, updates)

    def save_result_table(self, snapshot_id: str, name: str, row_indices: tuple[int, ...] | None = None) -> WorkspaceTable:
        return self._store.save_result_table(snapshot_id, name, row_indices)

    def result_snapshot(self, snapshot_id: str, row_indices: tuple[int, ...] | None = None) -> QueryResult:
        return self._store.result_snapshot(snapshot_id, row_indices)

    def export_result(self, snapshot_id: str, path: str | Path, format: str,
                      delimiter: str = DEFAULT_EXPORT_DELIMITER, row_indices: tuple[int, ...] | None = None) -> str:
        protected = (self.config.metadata_database, *(preset.storage.key for preset in self.list_presets() if preset.storage.mode is StorageMode.FILE))
        return self._store.export_result(snapshot_id, path, format, delimiter, row_indices, protected)

    def copy_result(self, snapshot_id: str, format: str, row_indices: tuple[int, ...] | None = None) -> str:
        return self._store.copy_result(snapshot_id, format, row_indices)

    def release_result(self, snapshot_id: str) -> None:
        self._store.release_result(snapshot_id)

    def interrupt_query(self) -> None:
        self._store.interrupt()

    def close(self) -> None:
        self._store.close()
