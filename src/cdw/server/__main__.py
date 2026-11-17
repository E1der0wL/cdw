"""Headless server entry point. Closing a GUI never terminates this process."""

from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

from cdw.config import PROFILE_LOCK_NAME, build_config
from cdw.domain.contracts import CdwError, ErrorCode
from cdw.infrastructure.profile_lock import ProfileLock
from cdw.infrastructure.server_wire import WireCodec
from cdw.server.endpoint import ServerEndpoint
from cdw.server.runtime import Runtime


class LocalServer:
    CONNECTION_WORKERS = 8
    ACCEPT_TIMEOUT = 0.25
    CONNECTION_TIMEOUT = 30
    BACKLOG = 32
    AUTH_MAX_BYTES = 4096
    AUTH_TIMEOUT_SECONDS = 2

    def __init__(self, config):
        self.config = config
        self.endpoint = ServerEndpoint(config)
        self.codec = WireCodec()
        self.stop_requested = threading.Event()
        self.token = secrets.token_hex(32)
        self.runtime = None
        self._connection_slots = threading.BoundedSemaphore(self.CONNECTION_WORKERS + self.BACKLOG)
        self._connections = set()
        self._connections_lock = threading.Lock()

    def serve(self):
        self.endpoint.prepare()
        lock = ProfileLock(self.config.data_directory / PROFILE_LOCK_NAME)
        lock.acquire()
        listener = None
        unix_path = None
        workers = ThreadPoolExecutor(max_workers=self.CONNECTION_WORKERS, thread_name_prefix="cdw-ipc")
        try:
            self.runtime = Runtime(self.config)
            if os.name == "nt":
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                listener.bind((ServerEndpoint.LOCAL_HOST, 0))
                descriptor = {"transport": "tcp", "port": listener.getsockname()[1]}
            else:
                unix_path = Path(self.endpoint.socket_address())
                if unix_path.exists():
                    unix_path.unlink()
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                listener.bind(str(unix_path))
                unix_path.chmod(0o600)
                descriptor = {"transport": "unix", "address": str(unix_path)}
            listener.listen(self.BACKLOG)
            listener.settimeout(self.ACCEPT_TIMEOUT)
            descriptor.update(token=self.token, pid=os.getpid(), version=self.codec.VERSION)
            temporary = self.endpoint.path.with_suffix(".new")
            with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as stream:
                json.dump(descriptor, stream)
            os.replace(temporary, self.endpoint.path)
            while not self.stop_requested.is_set():
                try:
                    connection, _ = listener.accept()
                except socket.timeout:
                    continue
                if self._connection_slots.acquire(blocking=False):
                    try:
                        with self._connections_lock:
                            self._connections.add(connection)
                        future = workers.submit(self._handle, connection)
                        future.add_done_callback(lambda _future: self._connection_slots.release())
                    except RuntimeError:
                        with self._connections_lock:
                            self._connections.discard(connection)
                        self._connection_slots.release()
                        connection.close()
                        raise
                else:
                    connection.close()
        finally:
            if listener is not None:
                listener.close()
            if self.runtime is not None:
                self.runtime.request_stop()
            with self._connections_lock:
                connections = tuple(self._connections)
            for connection in connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            workers.shutdown(wait=True)
            if self.runtime is not None:
                self.runtime.close()
            try:
                descriptor = self.endpoint.read()
                if descriptor.get("token") == self.token:
                    self.endpoint.path.unlink(missing_ok=True)
            except CdwError:
                pass
            if unix_path is not None:
                unix_path.unlink(missing_ok=True)
            lock.close()

    def _handle(self, connection):
        with connection:
            connection.settimeout(self.AUTH_TIMEOUT_SECONDS)
            try:
                credentials = self.codec.receive(connection, max_bytes=self.AUTH_MAX_BYTES)
                if not isinstance(credentials, dict) or credentials.get("version") != self.codec.VERSION or not hmac.compare_digest(str(credentials.get("token", "")), self.token):
                    return
                connection.settimeout(self.CONNECTION_TIMEOUT)
                request = self.codec.receive(connection)
                if request["method"] == "stop":
                    self.runtime.request_stop()
                    result = None
                else:
                    result = self.runtime.dispatch(request["method"], request.get("arguments", ()),
                                                   request.get("keywords", {}), request["session"])
                self.codec.send(connection, {"ok": True, "result": result})
                if request["method"] == "stop":
                    self.stop_requested.set()
            except Exception as error:
                code = error.code.value if isinstance(error, CdwError) else ErrorCode.INTERNAL.value
                details = error.details if isinstance(error, CdwError) else str(error)
                try:
                    self.codec.send(connection, {"ok": False, "error_code": code, "details": details})
                except (OSError, ValueError):
                    pass
            finally:
                with self._connections_lock:
                    self._connections.discard(connection)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args(argv)
    if os.name != "nt":
        os.umask(0o077)
    server = LocalServer(build_config(args.data_dir))
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda *_: server.stop_requested.set())
    try:
        server.serve()
    except CdwError as error:
        print(f"{error}\n{error.details}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
