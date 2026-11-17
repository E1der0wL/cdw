"""Process details terminate at this adapter; policy is supplied by CommandSpec."""

from __future__ import annotations

from cdw.i18n import tr

import os
import signal
import subprocess
import threading
import time

from cdw.domain.contracts import CdwError, CommandMode, CommandOutput, ErrorCode, CommandSpec


class SubprocessRunner:

    READ_CHUNK_BYTES = 64 * 1024
    POLL_SECONDS = 0.03
    TERMINATE_GRACE_SECONDS = 0.5
    READER_JOIN_SECONDS = 2.0
    WINDOWS_TERMINATE_SECONDS = 5
    WINDOWS_PROCESS_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    WINDOWS_KILL_COMMAND = "taskkill"
    INVALID_DIRECTORY_MESSAGE = tr('infrastructure.runner.invalid_directory_message')
    EXIT_CODE_MESSAGE = tr('infrastructure.runner.exit_code_message')
    SHELL_ARGUMENTS = ("-o", "pipefail", "-c")

    def run(self, command: CommandSpec, cancel: threading.Event, *, allow_nonzero: bool = False) -> CommandOutput:
        if cancel.is_set():
            raise CdwError(ErrorCode.CANCELLED)
        if command.working_directory and not os.path.isdir(command.working_directory):
            raise CdwError(ErrorCode.COMMAND_FAILED, SubprocessRunner.INVALID_DIRECTORY_MESSAGE)
        environment = dict(command.environment)
        arguments = ([command.executable, *self.SHELL_ARGUMENTS, command.script]
                     if command.mode is CommandMode.SHELL else [command.executable, *command.arguments])
        try:
            process = subprocess.Popen(
                arguments, cwd=command.working_directory,
                env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, shell=False, start_new_session=os.name != "nt",
                creationflags=SubprocessRunner.WINDOWS_PROCESS_FLAGS if os.name == "nt" else 0,
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
                while chunk := stream.read1(SubprocessRunner.READ_CHUNK_BYTES):
                    with counter_lock:
                        remaining = max(0, command.execution.max_output_bytes - total_bytes)
                        buffers[channel].extend(chunk[:remaining])
                        total_bytes += len(chunk)
                        if total_bytes > command.execution.max_output_bytes:
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
                elif time.monotonic() - started >= command.execution.timeout_seconds:
                    failure = ErrorCode.TIMEOUT
                if failure:
                    self._terminate(process)
                    break
                time.sleep(SubprocessRunner.POLL_SECONDS)
        finally:
            if process.poll() is None:
                self._terminate(process)
            for reader in readers:
                reader.join(SubprocessRunner.READER_JOIN_SECONDS)
            for stream, reader in zip((process.stdout, process.stderr), readers):
                if not reader.is_alive():
                    stream.close()
        stderr = bytes(buffers[1])
        diagnostic = stderr.decode(command.encoding, errors="replace")
        if failure:
            raise CdwError(failure, diagnostic)
        if overflow.is_set():
            raise CdwError(ErrorCode.OUTPUT_LIMIT, diagnostic)
        if cancel.is_set():
            raise CdwError(ErrorCode.CANCELLED, diagnostic)
        if reader_errors:
            raise CdwError(ErrorCode.COMMAND_FAILED, str(reader_errors[0]))
        if process.returncode != 0 and not allow_nonzero:
            raise CdwError(ErrorCode.COMMAND_FAILED,
                           SubprocessRunner.EXIT_CODE_MESSAGE.format(code=process.returncode) + "\n" + diagnostic)
        return CommandOutput(bytes(buffers[0]), stderr, process.returncode)

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        if os.name == "nt":
            try:
                subprocess.run(
                    [SubprocessRunner.WINDOWS_KILL_COMMAND, "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=SubprocessRunner.WINDOWS_TERMINATE_SECONDS, creationflags=SubprocessRunner.WINDOWS_PROCESS_FLAGS,
                )
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                time.sleep(SubprocessRunner.TERMINATE_GRACE_SECONDS)
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=SubprocessRunner.READER_JOIN_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
