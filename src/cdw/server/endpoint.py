"""Private endpoint discovery and detached lifecycle for the local CDW server."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from cdw.domain.contracts import CdwError, ErrorCode
from cdw.i18n import tr


class ServerEndpoint:
    DIRECTORY_NAME = ".runtime"
    ENDPOINT_NAME = "server.json"
    LOG_NAME = "server.log"
    START_TIMEOUT_SECONDS = 30
    POLL_SECONDS = 0.1
    SOCKET_TIMEOUT_SECONDS = 30
    LOCAL_HOST = "127.0.0.1"
    UNIX_PATH_LIMIT = 100

    def __init__(self, config):
        self.config = config
        self.directory = config.data_directory / self.DIRECTORY_NAME
        self.path = self.directory / self.ENDPOINT_NAME

    def prepare(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.directory.chmod(0o700)

    def socket_address(self):
        path = self.directory / "server.sock"
        if len(str(path).encode()) >= self.UNIX_PATH_LIMIT:
            directory = Path(tempfile.gettempdir()) / f"cdw-{os.getuid()}"
            directory.mkdir(mode=0o700, exist_ok=True)
            if directory.stat().st_uid != os.getuid():
                raise PermissionError(tr("server.socket_owner"))
            directory.chmod(0o700)
            digest = hashlib.sha256(str(self.config.data_directory).encode()).hexdigest()[:20]
            path = directory / f"{digest}.sock"
        return str(path)

    def read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise CdwError(ErrorCode.INTERNAL, tr("server.unavailable")) from error

    def connect(self, descriptor, *, timeout=None):
        unix = descriptor["transport"] == "unix"
        connection = socket.socket(socket.AF_UNIX if unix else socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout(self.SOCKET_TIMEOUT_SECONDS if timeout is None else timeout)
        try:
            connection.connect(descriptor["address"] if unix else (self.LOCAL_HOST, descriptor["port"]))
        except Exception:
            connection.close()
            raise
        return connection

    def launch(self):
        self.prepare()
        log = (self.directory / self.LOG_NAME).open("ab", buffering=0)
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        try:
            subprocess.Popen([sys.executable, "-m", "cdw.server", "--data-dir", str(self.config.data_directory)],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=os.name != "nt", creationflags=flags, close_fds=True)
        finally:
            log.close()

    def ensure_running(self, health):
        if health()["running"]:
            return
        self.launch()
        deadline = time.monotonic() + self.START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if health()["running"]:
                return
            time.sleep(self.POLL_SECONDS)
        raise CdwError(ErrorCode.INTERNAL, tr("server.start_failed").format(path=self.directory / self.LOG_NAME))
