from __future__ import annotations

from cdw.i18n import tr

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from cdw.config import AppConfig, DEFAULT_HISTORY_LIMIT
from cdw.domain.contracts import (
    CdwError, CollectionPreview, CollectionResult, ErrorCode, MEMORY_STORAGE_KEY, Preset, Progress,
    QueryResult, RunRecord, RunState, StorageMode, StorageTarget, TableInfo,
)
from cdw.infrastructure.duckdb_store import DuckDBStore
from cdw.infrastructure.metadata import MetadataRepository
from cdw.infrastructure.metadata_database import MetadataDatabase
from cdw.infrastructure.parsers import OutputParser
from cdw.infrastructure.runner import SubprocessRunner
from cdw.domain.appearance import AppearanceSettings
from cdw.domain.workspace import DEFAULT_EXPORT_DELIMITER, QuerySnapshot, WorkspaceTable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApplicationService:
    """Public synchronous API. Long operations run on one caller-owned worker.

    UI and CLI share these operations and their contracts. Metadata reads are
    short-lived connections; DuckDB calls must stay on one thread.
    """

    STAGE_MESSAGES = {
        RunState.RUNNING: tr('application.service.stage_messages'),
        RunState.PARSING: tr('application.service.stage_messages_2'),
        RunState.SAVING: tr('application.service.stage_messages_3'),
    }
    RECOVERED_MESSAGE = tr('application.service.recovered_message')
    INTERRUPTED_MESSAGE = tr('application.service.interrupted_message')
    COLLECTION_SQL = "SELECT * FROM {table}"


    def __init__(self, config: AppConfig, *, store=None) -> None:
        self.config = config
        self.database = MetadataDatabase(config.metadata_database)
        self.metadata = MetadataRepository(self.database)
        self._runner = SubprocessRunner()
        self._parser = OutputParser()
        self._store = store if store is not None else DuckDBStore()

    def preview(self, preset: Preset, cancel: threading.Event | None = None) -> CollectionPreview:
        """Execute the supplied definition and parse it without changing storage."""
        cancellation = cancel if cancel is not None else threading.Event()
        started = time.monotonic()
        output = self._runner.run(preset.command, cancellation, allow_nonzero=True)
        stdout = output.stdout.decode(preset.encoding, errors="replace")
        stderr = output.stderr.decode(preset.encoding, errors="replace")
        if output.return_code:
            return CollectionPreview(stdout, stderr, (), (), (), 0, time.monotonic() - started,
                                     tr("collection.preview_exit").format(code=output.return_code))
        try:
            frame = self._parser.parse(output.stdout, preset)
        except CdwError as error:
            return CollectionPreview(stdout, stderr, (), (), (), 0, time.monotonic() - started,
                                     f"{error}\n{error.details}".strip())
        return CollectionPreview(stdout, stderr, tuple(frame.columns), tuple(str(dtype) for dtype in frame.dtypes),
                                 tuple(frame.head(self.config.query_policy.preview_rows).iter_rows()), frame.height,
                                 time.monotonic() - started)

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
                details=ApplicationService.RECOVERED_MESSAGE if commit else ApplicationService.INTERRUPTED_MESSAGE,
            )

    def collect(self, preset_id: str, cancel: threading.Event | None = None,
                on_progress: Callable[[Progress], None] | None = None) -> CollectionResult:
        return self.collect_definition(self.get_preset(preset_id), cancel, on_progress)

    def collect_definition(self, preset: Preset, cancel: threading.Event | None = None,
                           on_progress: Callable[[Progress], None] | None = None) -> CollectionResult:
        """Run one captured definition even if the saved preset is edited later."""
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
                on_progress(Progress(run_id, state, ApplicationService.STAGE_MESSAGES[state]))

        try:
            stage(RunState.RUNNING)
            output = self._runner.run(preset.command, cancellation)
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
        finished_at = utc_now()
        self.metadata.finish_run(run_id, RunState.SUCCEEDED, finished_at, rows=inserted,
                                 elapsed=elapsed, stderr=stderr)
        return CollectionResult(run_id, inserted, total, elapsed, stderr, finished_at)

    def query(self, preset_id: str, sql: str, cancel: threading.Event | None = None) -> QueryResult:
        return self._store.query(self.get_preset(preset_id).storage, sql, self.config.query_policy, cancel)

    def list_tables(self, preset_id: str) -> list[TableInfo]:
        return self._store.tables(self.get_preset(preset_id).storage)

    def query_workspace(self, preset_id: str | None, sql: str, cancel: threading.Event | None = None) -> QuerySnapshot:
        target = self.get_preset(preset_id).storage if preset_id else None
        return self._store.query_workspace(target, sql, self.list_presets(), cancel)

    def collection_snapshot(self, preset: Preset, cancel: threading.Event | None = None) -> QuerySnapshot:
        """Read the exact target used by a completed collection."""
        return self._store.query_workspace(preset.storage, self.COLLECTION_SQL.format(table=preset.qualified_table), [preset], cancel)

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

    def interrupt_query(self, cancel: threading.Event | None = None) -> None:
        self._store.interrupt(cancel)

    def close(self) -> None:
        self._store.close()
