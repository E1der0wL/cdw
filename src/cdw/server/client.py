"""Local RPC client and public facades consumed by GUI and CLI."""

from __future__ import annotations

import threading
import time
from uuid import uuid4

from cdw.domain.contracts import CdwError, ErrorCode
from cdw.domain.execution import JobState
from cdw.domain.remote import RemoteApi
from cdw.domain.workspace import DEFAULT_EXPORT_DELIMITER
from cdw.infrastructure.server_wire import WireCodec
from cdw.i18n import tr
from cdw.server.endpoint import ServerEndpoint


class ServerClient:
    POLL_SECONDS = 0.1
    HEALTH_TIMEOUT_SECONDS = 1
    STOP_TIMEOUT_SECONDS = 30
    HEARTBEAT_SECONDS = 30

    def __init__(self, config):
        self.config = config
        self.session = str(uuid4())
        self.endpoint = ServerEndpoint(config)
        self.codec = WireCodec()
        self.detached = threading.Event()
        self._heartbeat_thread = None

    def call(self, method, *arguments, **keywords):
        descriptor = self.endpoint.read()
        try:
            timeout = self.HEALTH_TIMEOUT_SECONDS if method in ("status", "stop", "close_session", "heartbeat") else None
            with self.endpoint.connect(descriptor, timeout=timeout) as connection:
                self.codec.send(connection, {"version": self.codec.VERSION, "token": descriptor["token"]})
                self.codec.send(connection, {"session": self.session, "method": method,
                                             "arguments": arguments, "keywords": keywords})
                response = self.codec.receive(connection)
        except (OSError, ValueError, KeyError) as error:
            raise CdwError(ErrorCode.INTERNAL, tr("server.connection_failed").format(details=str(error))) from error
        if not response["ok"]:
            raise CdwError(ErrorCode(response["error_code"]), response["details"])
        return response["result"]

    def status(self):
        try:
            return self.call("status")
        except CdwError:
            return {"running": False, "pid": None, "started_at": None,
                    "data_directory": str(self.config.data_directory), "active_runs": 0}

    def start(self):
        self.endpoint.ensure_running(self.status)
        return self.status()

    def stop(self):
        """Wait for endpoint removal: memory and the profile lock are released."""
        status = self.status()
        if not status["running"] and status["pid"] is None:
            return status
        descriptor = self.endpoint.read()
        self.call("stop")
        deadline = time.monotonic() + self.STOP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                current = self.endpoint.read()
            except CdwError:
                return self.status()
            if current.get("token") != descriptor.get("token"):
                return self.status()
            time.sleep(self.POLL_SECONDS)
        raise CdwError(ErrorCode.INTERNAL, tr("server.stop_timeout"))

    def run(self, operation, *arguments, cancel=None, on_progress=None, **keywords):
        if self.detached.is_set():
            raise CdwError(ErrorCode.CANCELLED, tr("server.disconnected"))
        identifier = self.call("submit", operation, arguments, keywords)
        previous = None
        cancel_sent = False
        try:
            while not self.detached.is_set():
                if cancel is not None and cancel.is_set() and not cancel_sent:
                    cancel_sent = self.call("cancel", identifier)
                view = self.call("job", identifier)
                if view.progress != previous:
                    previous = view.progress
                    if on_progress and previous is not None:
                        on_progress(previous)
                if view.finished:
                    if view.state is JobState.SUCCEEDED:
                        return view.result
                    raise CdwError(ErrorCode(view.error_code), view.error_details)
                self.detached.wait(self.POLL_SECONDS)
            raise CdwError(ErrorCode.CANCELLED, tr("server.disconnected"))
        finally:
            if not self.detached.is_set():
                try:
                    self.call("forget_job", identifier)
                except CdwError:
                    pass

    def close(self):
        self.detached.set()
        try:
            self.call("close_session")
        except CdwError:
            pass

    def retain_session(self):
        """Keep an idle GUI's result tabs alive; crashed clients lose their lease."""
        if self._heartbeat_thread is None:
            self._heartbeat_thread = threading.Thread(target=self._heartbeat, name="cdw-client-lease", daemon=True)
            self._heartbeat_thread.start()

    def _heartbeat(self):
        while not self.detached.wait(self.HEARTBEAT_SECONDS):
            try:
                self.call("heartbeat")
            except CdwError:
                pass


class RemoteTaskService:
    def __init__(self, config, client):
        self.config = config
        self.client = client

    def list_tasks(self):
        return self.client.call("tasks.list_tasks")

    def get_task(self, task_id):
        return self.client.call("tasks.get_task", task_id)

    def save_task(self, task):
        return self.client.call("tasks.save_task", task)

    def delete_task(self, task_id):
        return self.client.call("tasks.delete_task", task_id)

    def run_task(self, task_id, result=None, cancel=None, *, context=None):
        return self.client.run("run_task", task_id, result, cancel=cancel, context=context)


class RemoteSchedulingService:

    def __init__(self, client):
        self.client = client

    def __getattr__(self, name):
        if name not in RemoteApi.SCHEDULE_METHODS:
            raise AttributeError(name)
        return lambda *args, **kwargs: self.client.call(f"schedules.{name}", *args, **kwargs)

    def server_status(self):
        return self.client.status()

    def start_server(self):
        return self.client.start()

    def stop_server(self):
        return self.client.stop()

    def run_workflow(self, workflow_id):
        return self.client.call("run_workflow", workflow_id)


class RemoteApplicationService:
    """Same application use cases, executed by the independent owning server."""

    def __init__(self, config, *, start=True):
        self.config = config
        self.client = ServerClient(config)
        self.tasks = RemoteTaskService(config, self.client)
        self.schedules = RemoteSchedulingService(self.client)
        if start:
            self.client.start()
        self.client.retain_session()

    def __getattr__(self, name):
        if name not in RemoteApi.SERVICE_METHODS:
            raise AttributeError(name)
        return lambda *args, **kwargs: self.client.call(f"service.{name}", *args, **kwargs)

    def recover(self):
        # Recovery is owned by server startup. GUI boot only verifies connection.
        if not self.client.status()["running"]:
            raise CdwError(ErrorCode.INTERNAL, tr("server.unavailable"))

    def collect(self, preset_id, cancel=None, on_progress=None):
        return self.client.run("collect", preset_id, cancel=cancel, on_progress=on_progress)

    def preview(self, preset, cancel=None):
        return self.client.run("preview", preset, cancel=cancel)

    def query(self, preset_id, sql, cancel=None):
        return self.client.run("query", preset_id, sql, cancel=cancel)

    def query_workspace(self, preset_id, sql, cancel=None):
        return self.client.run("query_workspace", preset_id, sql, cancel=cancel)

    def copy_result(self, snapshot_id, format, row_indices=None):
        return self.client.run("copy_result", snapshot_id, format, row_indices=row_indices)

    def export_result(self, snapshot_id, path, format, delimiter=DEFAULT_EXPORT_DELIMITER, row_indices=None):
        return self.client.run("export_result", snapshot_id, path, format, delimiter=delimiter, row_indices=row_indices)

    def save_result_table(self, snapshot_id, name, row_indices=None):
        return self.client.run("save_result_table", snapshot_id, name, row_indices=row_indices)

    def interrupt_query(self):
        # The wait loop propagates the controller's cancellation Event to its job.
        pass

    def detach(self):
        self.client.detached.set()

    def close(self):
        self.client.close()
