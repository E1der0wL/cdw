"""Single owner thread for DuckDB, independent of subprocess wait times."""

from concurrent.futures import ThreadPoolExecutor
import threading

from cdw.domain.contracts import CdwError, ErrorCode
from cdw.infrastructure.duckdb_store import DuckDBStore


class DatabaseWorker:
    THREAD_NAME = "cdw-database"
    METHODS = frozenset(("write", "query", "tables", "committed", "query_workspace", "workspace_tables",
                         "save_result_table", "result_snapshot", "export_result", "copy_result", "release_result"))
    QUERY_METHODS = frozenset(("query", "query_workspace"))
    CANCEL_POSITION = 3
    CANCELLATION_POLL_SECONDS = 0.05

    def __init__(self):
        self._store = DuckDBStore()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=self.THREAD_NAME)
        self._closed = False
        self._mutex = threading.Lock()
        self._query_active = False
        self._active_cancel = None
        self._monitor_stop = threading.Event()
        self._monitor = threading.Thread(target=self._watch_cancellation, name="cdw-query-cancel", daemon=True)
        self._monitor.start()

    def __getattr__(self, name):
        if name not in self.METHODS:
            raise AttributeError(name)
        def call(*args, **kwargs):
            return self._executor.submit(self._execute, name, args, kwargs).result()
        return call

    def _execute(self, name, args, kwargs):
        query = name in self.QUERY_METHODS
        cancel = kwargs.get("cancel", args[self.CANCEL_POSITION] if query and len(args) > self.CANCEL_POSITION else None)
        with self._mutex:
            if cancel is not None and cancel.is_set():
                raise CdwError(ErrorCode.CANCELLED)
            self._query_active = query
            self._active_cancel = cancel if query else None
        try:
            return getattr(self._store, name)(*args, **kwargs)
        finally:
            with self._mutex:
                self._query_active = False
                self._active_cancel = None

    def interrupt(self, cancel=None):
        with self._mutex:
            if self._query_active and (cancel is None or cancel is self._active_cancel):
                self._store.interrupt(cancel)

    def _watch_cancellation(self):
        # A cancel can race with DuckDB starting its next internal statement.
        # Repeat only for the active query token until that operation finishes.
        while not self._monitor_stop.wait(self.CANCELLATION_POLL_SECONDS):
            with self._mutex:
                if self._query_active and self._active_cancel is not None and self._active_cancel.is_set():
                    self._store.interrupt(self._active_cancel)

    def close(self):
        if self._closed:
            return
        try:
            self._executor.submit(self._store.close).result()
        finally:
            self._monitor_stop.set()
            self._monitor.join()
            self._executor.shutdown(wait=True)
            self._closed = True
