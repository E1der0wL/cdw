"""SQLite persistence for task definitions, separate from data collection."""

from __future__ import annotations

from cdw.i18n import tr

import json
import sqlite3

from cdw.domain.contracts import CdwError, ErrorCode
from cdw.domain.tasks import TaskDefinition
from cdw.infrastructure.metadata_database import MetadataDatabase


class TaskRepository:

    SAVE_SQL = """INSERT INTO workspace_tasks VALUES (?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET name=excluded.name, definition=excluded.definition"""
    LIST_SQL = "SELECT definition FROM workspace_tasks ORDER BY name COLLATE NOCASE"
    GET_SQL = "SELECT definition FROM workspace_tasks WHERE id=?"
    DELETE_SQL = "DELETE FROM workspace_tasks WHERE id=?"
    DUPLICATE_TASK = tr('infrastructure.task_repository.duplicate_task')
    INVALID_SAVED_TASK = tr('infrastructure.task_repository.invalid_saved_task')
    TASK_NOT_FOUND = tr('infrastructure.task_repository.task_not_found')

    def __init__(self, database: MetadataDatabase):
        self.database = database

    @staticmethod
    def _decode(text: str) -> TaskDefinition:
        try:
            return TaskDefinition.from_dict(json.loads(text))
        except (ValueError, TypeError, KeyError, CdwError) as exc:
            raise CdwError(ErrorCode.STORAGE_FAILED, TaskRepository.INVALID_SAVED_TASK) from exc

    def list_tasks(self) -> list[TaskDefinition]:
        with self.database.connect() as connection:
            return [self._decode(row[0]) for row in connection.execute(TaskRepository.LIST_SQL)]

    def get_task(self, task_id: str) -> TaskDefinition:
        with self.database.connect() as connection:
            row = connection.execute(TaskRepository.GET_SQL, (task_id,)).fetchone()
        if row is None:
            raise CdwError(ErrorCode.NOT_FOUND, TaskRepository.TASK_NOT_FOUND)
        return self._decode(row[0])

    def save_task(self, task: TaskDefinition) -> TaskDefinition:
        with self.database.connect() as connection:
            try:
                connection.execute(TaskRepository.SAVE_SQL, (task.id, task.name, json.dumps(task.to_dict(), ensure_ascii=False)))
            except sqlite3.IntegrityError as exc:
                raise CdwError(ErrorCode.STORAGE_FAILED, TaskRepository.DUPLICATE_TASK) from exc
        return task

    def delete_task(self, task_id: str):
        with self.database.connect() as connection:
            cursor = connection.execute(TaskRepository.DELETE_SQL, (task_id,))
            if not cursor.rowcount:
                raise CdwError(ErrorCode.NOT_FOUND, TaskRepository.TASK_NOT_FOUND)
