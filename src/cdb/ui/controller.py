from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from typing import Callable

from qtpy.QtCore import QObject, Signal

from cdb.application.service import ApplicationService
from cdb.domain.contracts import CdbError, ErrorCode

WORKER_COUNT = 1
WORKER_NAME = "cdb-engine"
PREVIEW_SQL = "SELECT * FROM {table};"


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


class Controller(QObject):
    completed = Signal(str, object)
    failed = Signal(str, object)
    progress = Signal(object)

    def __init__(self, service: ApplicationService) -> None:
        super().__init__()
        self.service = service
        self._executor = ThreadPoolExecutor(max_workers=WORKER_COUNT, thread_name_prefix=WORKER_NAME)
        self._cancel = threading.Event()
        self.busy = False
        self.operation = ""

    def submit(self, operation: str, work: Callable) -> bool:
        if self.busy:
            return False
        self.busy = True
        self.operation = operation
        self._cancel = threading.Event()
        future = self._executor.submit(work)

        def finished(result):
            try:
                value = result.result()
            except Exception as exc:
                error = exc if isinstance(exc, CdbError) else CdbError(ErrorCode.INTERNAL, str(exc))
                self.failed.emit(operation, error)
            else:
                self.completed.emit(operation, value)

        future.add_done_callback(finished)
        return True

    def collect(self, preset_id: str) -> bool:
        def work():
            result = self.service.collect(preset_id, self._cancel, self.progress.emit)
            return result, self.service.workspace_tables()
        return self.submit(Operation.COLLECT, work)

    def context(self, preset_id: str | None = None) -> bool:
        def work():
            return self.service.workspace_tables()
        return self.submit(Operation.CONTEXT, work)

    def query(self, preset_id: str | None, sql: str) -> bool:
        return self.submit(Operation.QUERY, lambda: self.service.query_workspace(preset_id, sql, self._cancel))

    def copy_result(self, snapshot_id, format, row_indices):
        def work():
            text = self.service.copy_result(snapshot_id, "text", row_indices=row_indices)
            html = self.service.copy_result(snapshot_id, "html", row_indices=row_indices) if format == "html" else None
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

    def run_task(self, task_service, task_id, result):
        return self.submit(Operation.TASK, lambda: task_service.run_task(task_id, result, self._cancel))

    def release_result(self, snapshot_id):
        return self.submit(Operation.RELEASE_RESULT, lambda: self.service.release_result(snapshot_id))

    def release(self) -> None:
        # Called by the GUI after receiving the queued completion signal.
        self.busy = False
        self.operation = ""

    def cancel(self) -> None:
        self._cancel.set()
        if self.operation == Operation.QUERY:
            self.service.interrupt_query()

    def shutdown(self) -> None:
        self._executor.submit(self.service.close).result()
        self._executor.shutdown(wait=True)
