"""Process details terminate at this adapter; policy is supplied by Preset."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time

from cdw.domain.contracts import CdwError, CommandOutput, ErrorCode, Preset

READ_CHUNK_BYTES = 64 * 1024
POLL_SECONDS = 0.03
TERMINATE_GRACE_SECONDS = 0.5
READER_JOIN_SECONDS = 2.0
WINDOWS_TERMINATE_SECONDS = 5
WINDOWS_PROCESS_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
WINDOWS_KILL_COMMAND = "taskkill"
INVALID_DIRECTORY_MESSAGE = "작업 디렉터리가 존재하지 않습니다."
EXIT_CODE_MESSAGE = "종료 코드: {code}"


class SubprocessRunner:
    def run(self, preset: Preset, cancel: threading.Event) -> CommandOutput:
        if cancel.is_set():
            raise CdwError(ErrorCode.CANCELLED)
        if preset.working_directory and not os.path.isdir(preset.working_directory):
            raise CdwError(ErrorCode.COMMAND_FAILED, INVALID_DIRECTORY_MESSAGE)
        environment = os.environ.copy()
        environment.update(preset.environment)
        try:
            process = subprocess.Popen(
                [preset.executable, *preset.arguments], cwd=preset.working_directory,
                env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, shell=False, start_new_session=os.name != "nt",
                creationflags=WINDOWS_PROCESS_FLAGS if os.name == "nt" else 0,
            )
        except (OSError, ValueError) as exc:
            raise CdwError(ErrorCode.COMMAND_FAILED, str(exc)) from exc

        buffers = [bytearray(), bytearray()]
        overflow = threading.Event()
        reader_errors: list[Exception] = []
        counter_lock = threading.Lock()
        total_bytes = 0

        def drain(stream, channel: int) -> None:
            nonlocal total_bytes
            try:
                while chunk := stream.read1(READ_CHUNK_BYTES):
                    with counter_lock:
                        remaining = max(0, preset.execution.max_output_bytes - total_bytes)
                        buffers[channel].extend(chunk[:remaining])
                        total_bytes += len(chunk)
                        if total_bytes > preset.execution.max_output_bytes:
                            overflow.set()
                            return
            except (OSError, ValueError) as exc:
                reader_errors.append(exc)

        readers = [threading.Thread(target=drain, args=(stream, index), daemon=True)
                   for index, stream in enumerate((process.stdout, process.stderr))]
        started = time.monotonic()
        failure: ErrorCode | None = None
        try:
            for reader in readers:
                reader.start()
            while process.poll() is None or any(reader.is_alive() for reader in readers):
                if cancel.is_set():
                    failure = ErrorCode.CANCELLED
                elif overflow.is_set():
                    failure = ErrorCode.OUTPUT_LIMIT
                elif time.monotonic() - started >= preset.execution.timeout_seconds:
                    failure = ErrorCode.TIMEOUT
                if failure:
                    self._terminate(process)
                    break
                time.sleep(POLL_SECONDS)
        finally:
            if process.poll() is None:
                self._terminate(process)
            for reader in readers:
                reader.join(READER_JOIN_SECONDS)
            for stream, reader in zip((process.stdout, process.stderr), readers):
                if not reader.is_alive():
                    stream.close()
        stderr = bytes(buffers[1])
        diagnostic = stderr.decode(preset.encoding, errors="replace")
        if failure:
            raise CdwError(failure, diagnostic)
        if overflow.is_set():
            raise CdwError(ErrorCode.OUTPUT_LIMIT, diagnostic)
        if cancel.is_set():
            raise CdwError(ErrorCode.CANCELLED, diagnostic)
        if reader_errors:
            raise CdwError(ErrorCode.COMMAND_FAILED, str(reader_errors[0]))
        if process.returncode != 0:
            raise CdwError(ErrorCode.COMMAND_FAILED,
                           EXIT_CODE_MESSAGE.format(code=process.returncode) + "\n" + diagnostic)
        return CommandOutput(bytes(buffers[0]), stderr, process.returncode)

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        if os.name == "nt":
            try:
                subprocess.run(
                    [WINDOWS_KILL_COMMAND, "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=WINDOWS_TERMINATE_SECONDS, creationflags=WINDOWS_PROCESS_FLAGS,
                )
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                time.sleep(TERMINATE_GRACE_SECONDS)
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=READER_JOIN_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
