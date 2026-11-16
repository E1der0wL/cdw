"""Table-file lifetime and process execution terminate at this adapter."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from cdw.domain.contracts import CdwError, ErrorCode, QueryResult
from cdw.domain.tasks import TASK_ENCODING, TaskDefinition, TaskInputFormat, TaskRunResult
from cdw.infrastructure.result_exports import EXPORT_ENCODING, csv_text, json_text
from cdw.infrastructure.runner import SubprocessRunner

TASK_TEMP_PREFIX = "cdw-task-"
TASK_INPUT_STEM = "input"
TRUNCATED_RESULT = "잘린 미리보기 결과는 작업 입력으로 사용할 수 없습니다. 전체 결과를 조회해 주세요."


class TableTaskRunner:
    def __init__(self):
        self._runner = SubprocessRunner()

    def run(self, task: TaskDefinition, result: QueryResult, cancel: threading.Event | None = None) -> TaskRunResult:
        cancellation = cancel if cancel is not None else threading.Event()
        if cancellation.is_set():
            raise CdwError(ErrorCode.CANCELLED)
        if result.truncated:
            raise CdwError(ErrorCode.INVALID_QUERY, TRUNCATED_RESULT)
        started = time.monotonic()
        try:
            with TemporaryDirectory(prefix=TASK_TEMP_PREFIX) as directory:
                path = Path(directory) / f"{TASK_INPUT_STEM}.{task.input_format.value}"
                text = (json_text(result, envelope=True) if task.input_format is TaskInputFormat.JSON
                        else csv_text(result, task.csv_delimiter))
                path.write_text(text, encoding=EXPORT_ENCODING, newline="")
                output = self._runner.run(task.command_for_input(str(path)), cancellation)
        except (OSError, UnicodeError) as exc:
            raise CdwError(ErrorCode.STORAGE_FAILED, str(exc)) from exc
        return TaskRunResult(
            task.id, task.name, output.stdout.decode(TASK_ENCODING, errors="replace"),
            output.stderr.decode(TASK_ENCODING, errors="replace"), time.monotonic() - started, output.return_code,
        )
