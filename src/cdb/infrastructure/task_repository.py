"""SQLite persistence for task definitions, separate from data collection."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from cdb.domain.contracts import CdbError, ErrorCode
from cdb.domain.tasks import TaskDefinition

SQLITE_TIMEOUT_SECONDS = 10
CREATE_TABLE_SQL = """CREATE TABLE IF NOT EXISTS workspace_tasks (
    id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE, definition TEXT NOT NULL
)"""
SAVE_SQL = """INSERT INTO workspace_tasks VALUES (?, ?, ?)
ON CONFLICT(id) DO UPDATE SET name=excluded.name, definition=excluded.definition"""
LIST_SQL = "SELECT definition FROM workspace_tasks ORDER BY name COLLATE NOCASE"
GET_SQL = "SELECT definition FROM workspace_tasks WHERE id=?"
DELETE_SQL = "DELETE FROM workspace_tasks WHERE id=?"
DUPLICATE_TASK = "같은 이름의 작업이 있습니다. 다른 이름을 지정해 주세요."
INVALID_SAVED_TASK = "저장된 작업 설정을 읽지 못했습니다."
TASK_NOT_FOUND = "작업을 찾을 수 없습니다. 목록을 새로고침해 주세요."


class TaskRepository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(CREATE_TABLE_SQL)

    @contextmanager
    def _connect(self):
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=SQLITE_TIMEOUT_SECONDS)
            with connection:
                yield connection
        except sqlite3.IntegrityError as exc:
            raise CdbError(ErrorCode.STORAGE_FAILED, DUPLICATE_TASK) from exc
        except sqlite3.Error as exc:
            raise CdbError(ErrorCode.STORAGE_FAILED, str(exc)) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _decode(text: str) -> TaskDefinition:
        try:
            return TaskDefinition.from_dict(json.loads(text))
        except (ValueError, TypeError, KeyError, CdbError) as exc:
            raise CdbError(ErrorCode.STORAGE_FAILED, INVALID_SAVED_TASK) from exc

    def list_tasks(self) -> list[TaskDefinition]:
        with self._connect() as connection:
            return [self._decode(row[0]) for row in connection.execute(LIST_SQL)]

    def get_task(self, task_id: str) -> TaskDefinition:
        with self._connect() as connection:
            row = connection.execute(GET_SQL, (task_id,)).fetchone()
        if row is None:
            raise CdbError(ErrorCode.NOT_FOUND, TASK_NOT_FOUND)
        return self._decode(row[0])

    def save_task(self, task: TaskDefinition) -> TaskDefinition:
        with self._connect() as connection:
            connection.execute(SAVE_SQL, (task.id, task.name, json.dumps(task.to_dict(), ensure_ascii=False)))
        return task

    def delete_task(self, task_id: str):
        with self._connect() as connection:
            cursor = connection.execute(DELETE_SQL, (task_id,))
            if not cursor.rowcount:
                raise CdbError(ErrorCode.NOT_FOUND, TASK_NOT_FOUND)
