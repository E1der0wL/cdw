"""Client-visible cancellation, shutdown and lossless IPC contracts."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as clock_time, timedelta, timezone
from decimal import Decimal
import socket
import time
from uuid import uuid4
import threading
from types import SimpleNamespace

import pytest

from cdw.config import build_config
from cdw.domain.contracts import CdwError, ErrorCode
from cdw.domain.execution import JobState
from cdw.infrastructure.server_wire import WireCodec
from cdw.server.runtime import Runtime
from cdw.server.__main__ import LocalServer


LONG_SQL = "SELECT sum(sqrt(i)) FROM range(1000000000000) AS t(i)"
SESSION = "runtime-test-client"
WAIT_SECONDS = 12


def wait_until(check):
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError("Execution did not reach the expected state")


@pytest.fixture
def runtime(tmp_path):
    instance = Runtime(build_config(tmp_path))
    try:
        yield instance
    finally:
        instance.close()


def view(runtime, identity):
    return runtime.dispatch("job", (identity,), {}, SESSION)


def submit(runtime, sql):
    return runtime.dispatch("submit", ("query_workspace", (None, sql), {}), {}, SESSION)


def test_queued_query_cancellation_never_interrupts_another_query(runtime):
    active = submit(runtime, LONG_SQL)
    wait_until(lambda: view(runtime, active).state is JobState.RUNNING)
    queued = submit(runtime, "SELECT 7 AS queued_value")
    assert runtime.dispatch("cancel", (queued,), {}, SESSION)
    time.sleep(0.15)
    assert not view(runtime, active).finished
    assert runtime.dispatch("cancel", (active,), {}, SESSION)
    first = wait_until(lambda: (v if (v := view(runtime, active)).finished else None))
    second = wait_until(lambda: (v if (v := view(runtime, queued)).finished else None))
    assert first.state is second.state is JobState.CANCELLED
    following = submit(runtime, "SELECT 42 AS answer")
    result = wait_until(lambda: (v if (v := view(runtime, following)).finished else None))
    assert result.state is JobState.SUCCEEDED, result.error_details
    assert result.result.result.rows == ((42,),)


def test_stop_unblocks_metadata_waiting_behind_long_query(runtime):
    active = submit(runtime, LONG_SQL)
    wait_until(lambda: view(runtime, active).state is JobState.RUNNING)
    with ThreadPoolExecutor(max_workers=1) as pool:
        listing = pool.submit(runtime.dispatch, "service.workspace_tables", (), {}, SESSION)
        try:
            time.sleep(0.1)
            runtime.request_stop()
            assert listing.result(timeout=WAIT_SECONDS) == []
            assert wait_until(lambda: view(runtime, active).finished)
        finally:
            runtime.request_stop()


def test_one_session_cannot_cancel_or_read_another_sessions_job(runtime):
    identity = submit(runtime, LONG_SQL)
    try:
        assert not runtime.dispatch("cancel", (identity,), {}, "different-session")
        with pytest.raises(CdwError) as error:
            runtime.dispatch("job", (identity,), {}, "different-session")
        assert error.value.code is ErrorCode.NOT_FOUND
    finally:
        runtime.dispatch("cancel", (identity,), {}, SESSION)


def test_wire_preserves_sql_values_and_literal_tag_keys():
    codec = WireCodec()
    values = (
        Decimal("123456789012345678901234.123456789"), 2**100, b"\x00\xff",
        date(2026, 11, 17), datetime(2026, 11, 17, 1, tzinfo=timezone.utc), clock_time(12, 30),
        timedelta(days=-2, seconds=1, microseconds=25), uuid4(), None,
        {"$type": "ordinary SQL STRUCT key", "$tuple": [1, 2], "$map": {"nested": 3}},
    )
    left, right = socket.socketpair()
    try:
        codec.send(left, values)
        assert codec.receive(right) == values
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize("length", (0, WireCodec.MAX_MESSAGE_BYTES + 1))
def test_wire_rejects_invalid_frame_size_before_reading_payload(length):
    left, right = socket.socketpair()
    try:
        left.sendall(WireCodec.HEADER.pack(length))
        with pytest.raises(ValueError):
            WireCodec().receive(right)
    finally:
        left.close()
        right.close()


def test_wire_never_imports_a_requested_type():
    with pytest.raises(KeyError):
        WireCodec().decode({"$type": "subprocess.Popen", "fields": {"args": ["unused"]}})


def test_invalid_authentication_never_reads_or_executes_request(tmp_path):
    server = LocalServer(build_config(tmp_path))
    executed = []
    server.runtime = SimpleNamespace(dispatch=lambda *args: executed.append(args))
    client, connection = socket.socketpair()
    client.settimeout(2)
    worker = threading.Thread(target=server._handle, args=(connection,))
    worker.start()
    try:
        server.codec.send(client, {"version": server.codec.VERSION, "token": "incorrect-token"})
        assert client.recv(1) == b""
        assert executed == []
    finally:
        client.close()
        worker.join(timeout=3)
        assert not worker.is_alive()


def test_authentication_has_small_frame_limit(tmp_path):
    server = LocalServer(build_config(tmp_path))
    executed = []
    server.runtime = SimpleNamespace(dispatch=lambda *args: executed.append(args))
    client, connection = socket.socketpair()
    client.settimeout(2)
    worker = threading.Thread(target=server._handle, args=(connection,))
    worker.start()
    try:
        client.sendall(WireCodec.HEADER.pack(LocalServer.AUTH_MAX_BYTES + 1))
        response = server.codec.receive(client)
        assert response["ok"] is False
        assert executed == []
    finally:
        client.close()
        worker.join(timeout=3)
        assert not worker.is_alive()


def test_expired_result_session_releases_tabs_but_preserves_saved_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(Runtime, "SESSION_IDLE_SECONDS", 0.2)
    monkeypatch.setattr(Runtime, "SESSION_CLEANUP_SECONDS", 0.01)
    monkeypatch.setattr(Runtime, "TICK_SECONDS", 0.02)
    runtime = Runtime(build_config(tmp_path))
    try:
        identity = submit(runtime, "SELECT 17 AS value")
        done = wait_until(lambda: (v if (v := view(runtime, identity)).finished else None))
        snapshot = done.result
        runtime.service.save_result_table(snapshot.id, "kept")
        time.sleep(0.4)
        with pytest.raises(CdwError) as error:
            runtime.service.result_snapshot(snapshot.id)
        assert error.value.code is ErrorCode.NOT_FOUND
        result = runtime.service.query_workspace(None, "SELECT * FROM memory.kept")
        assert result.result.rows == ((17,),)
        runtime.service.release_result(result.id)
    finally:
        runtime.close()
