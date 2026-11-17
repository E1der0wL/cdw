"""GUI-thread execution state; closing this client never stops server jobs."""
from __future__ import annotations

from cdw.i18n import tr

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Callable

from qtpy.QtCore import QObject, QThread, Qt, Signal, Slot
from cdw.application.service import ApplicationService
from cdw.application.tasks import TaskService
from cdw.domain.contracts import CdwError, ErrorCode, RunState
from cdw.domain.workspace import CopyFormat


class Operation(StrEnum):
    BOOT = "boot"
    COLLECT = "collect"
    CONTEXT = "context"
    QUERY = "query"
    COPY = "copy"
    EXPORT = "export"
    SAVE_TABLE = "save_table"
    TASK = "task"
    RELEASE_RESULT = "release_result"


@dataclass(frozen=True)
class ExecutionState:
    CANCELLABLE_OPERATIONS = frozenset((Operation.COLLECT, Operation.QUERY, Operation.TASK))

    operation: Operation | None = None
    stage: RunState | None = None
    cancellation_requested: bool = False

    @property
    def busy(self):
        return self.operation is not None

    @property
    def can_cancel(self):
        return (self.operation in self.CANCELLABLE_OPERATIONS
                and self.stage is not RunState.SAVING and not self.cancellation_requested)


class Controller(QObject):
    WORKER_COUNT = 1
    WORKER_NAME = "cdw-engine"
    THREAD_ERROR = tr("execution.thread_error")
    completed = Signal(str, object)
    failed = Signal(str, object)
    progress = Signal(object)
    state_changed = Signal(object)
    _finished = Signal(object, object, object)
    _progress_received = Signal(object)

    def __init__(self, service: ApplicationService, task_service: TaskService):
        super().__init__()
        self.service = service
        self.task_service = task_service
        self._executor = ThreadPoolExecutor(max_workers=self.WORKER_COUNT, thread_name_prefix=self.WORKER_NAME)
        self._cancel = threading.Event()
        self._state = ExecutionState()
        self._accepting = True
        self._closed = False
        self._finished.connect(self._finish, Qt.QueuedConnection)
        self._progress_received.connect(self._update_progress, Qt.QueuedConnection)

    @property
    def state(self):
        return self._state

    @property
    def busy(self):
        return self.state.busy

    @property
    def accepting(self):
        return self._accepting

    def _set_state(self, state):
        self._state = state
        self.state_changed.emit(state)

    def _assert_owner(self):
        if QThread.currentThread() != self.thread():
            raise RuntimeError(self.THREAD_ERROR)

    def submit(self, operation: Operation, work: Callable) -> bool:
        self._assert_owner()
        if self.busy or not self.accepting:
            return False
        self._cancel = threading.Event()
        self._set_state(ExecutionState(operation=Operation(operation)))
        try:
            future = self._executor.submit(work)
        except RuntimeError as exc:
            self._finish(operation, None, CdwError(ErrorCode.INTERNAL, str(exc)))
            return False

        def finished(result):
            try:
                value = result.result()
            except Exception as exc:
                error = exc if isinstance(exc, CdwError) else CdwError(ErrorCode.INTERNAL, str(exc))
                self._finished.emit(operation, None, error)
            else:
                self._finished.emit(operation, value, None)
        future.add_done_callback(finished)
        return True

    @Slot(object, object, object)
    def _finish(self, operation, value, error):
        if self._closed:
            return
        self._set_state(ExecutionState())
        if error is None:
            self.completed.emit(operation, value)
        else:
            self.failed.emit(operation, error)

    @Slot(object)
    def _update_progress(self, progress):
        if self._closed:
            return
        self._set_state(replace(self.state, stage=progress.state))
        self.progress.emit(progress)

    def collect(self, preset_id):
        def work():
            result = self.service.collect(preset_id, self._cancel, self._progress_received.emit)
            return result, self.service.workspace_tables()
        return self.submit(Operation.COLLECT, work)

    def context(self):
        return self.submit(Operation.CONTEXT, self.service.workspace_tables)

    def query(self, preset_id, sql):
        return self.submit(Operation.QUERY, lambda: self.service.query_workspace(preset_id, sql, self._cancel))

    def copy_result(self, snapshot_id, format, row_indices):
        def work():
            text = self.service.copy_result(snapshot_id, CopyFormat.TEXT, row_indices=row_indices)
            html = self.service.copy_result(snapshot_id, CopyFormat.HTML, row_indices=row_indices) if format == CopyFormat.HTML else None
            return text, html
        return self.submit(Operation.COPY, work)

    def export_result(self, snapshot_id, path, format, delimiter, row_indices):
        return self.submit(Operation.EXPORT, lambda: self.service.export_result(
            snapshot_id, path, format, delimiter=delimiter, row_indices=row_indices))

    def save_result_table(self, snapshot_id, name, row_indices):
        def work():
            table = self.service.save_result_table(snapshot_id, name, row_indices=row_indices)
            return table, self.service.workspace_tables()
        return self.submit(Operation.SAVE_TABLE, work)

    def run_task(self, task_id, result=None):
        return self.submit(Operation.TASK, lambda: self.task_service.run_task(task_id, result, self._cancel))

    def release_result(self, snapshot_id):
        return self.submit(Operation.RELEASE_RESULT, lambda: self.service.release_result(snapshot_id))

    def cancel(self):
        self._assert_owner()
        if not self.state.can_cancel:
            return False
        self._cancel.set()
        self._set_state(replace(self.state, cancellation_requested=True))
        if self.state.operation == Operation.QUERY:
            self.service.interrupt_query()
        return True

    def stop_accepting(self):
        self._assert_owner()
        self._accepting = False
        self.cancel()

    def shutdown(self):
        self._assert_owner()
        if self._closed:
            return
        self._accepting = False
        self._closed = True
        try:
            self._executor.submit(self.service.close).result()
        finally:
            self._executor.shutdown(wait=True)
