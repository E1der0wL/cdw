"""Table-file lifetime and process execution terminate at this adapter."""

from __future__ import annotations

from cdw.i18n import tr

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from cdw.domain.contracts import CdwError, ErrorCode, QueryResult
from cdw.domain.tasks import TASK_ENCODING, TaskDefinition, TaskInputContext, TaskInputFormat, TaskRunResult
from cdw.infrastructure.result_exports import EXPORT_ENCODING, csv_text
from cdw.infrastructure.runner import SubprocessRunner
from cdw.infrastructure.task_protocol import TaskTableProtocol


class TableTaskRunner:

    TASK_TEMP_PREFIX = "cdw-task-"
    TASK_INPUT_STEM = "input"
    TRUNCATED_RESULT = tr('infrastructure.task_runner.truncated_result')
    INPUT_REQUIRED = tr("tasks.runner.input_required")

    def __init__(self):
        self._runner = SubprocessRunner()

    def run(self, task: TaskDefinition, result: QueryResult | None = None,
            cancel: threading.Event | None = None, *, context: TaskInputContext | None = None) -> TaskRunResult:
        cancellation = cancel if cancel is not None else threading.Event()
        if cancellation.is_set():
            raise CdwError(ErrorCode.CANCELLED)
        if task.requires_input and result is None:
            raise CdwError(ErrorCode.INVALID_QUERY, self.INPUT_REQUIRED)
        if task.requires_input and result.truncated:
            raise CdwError(ErrorCode.INVALID_QUERY, TableTaskRunner.TRUNCATED_RESULT)
        started = time.monotonic()
        try:
            if not task.requires_input:
                output = self._runner.run(task.command, cancellation)
            else:
                snapshot_context = context or TaskInputContext(str(uuid4()), "", datetime.now(timezone.utc).isoformat())
                with TemporaryDirectory(prefix=TableTaskRunner.TASK_TEMP_PREFIX) as directory:
                    path = Path(directory) / f"{TableTaskRunner.TASK_INPUT_STEM}.{task.input_format.value}"
                    if task.input_format is TaskInputFormat.JSON:
                        text, encoding = TaskTableProtocol.serialize(result, snapshot_context), TaskTableProtocol.ENCODING
                    else:
                        text, encoding = csv_text(result, task.csv_delimiter), EXPORT_ENCODING
                    path.write_text(text, encoding=encoding, newline="")
                    output = self._runner.run(task.command_for_input(str(path)), cancellation)
        except (OSError, UnicodeError) as exc:
            raise CdwError(ErrorCode.STORAGE_FAILED, str(exc)) from exc
        return TaskRunResult(
            task.id, task.name, output.stdout.decode(TASK_ENCODING, errors="replace"),
            output.stderr.decode(TASK_ENCODING, errors="replace"), time.monotonic() - started, output.return_code,
        )
