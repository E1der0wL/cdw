"""Public task configuration and table-driven execution use cases."""

from __future__ import annotations

import threading

from cdw.config import AppConfig
from cdw.domain.contracts import QueryResult
from cdw.domain.tasks import TaskDefinition, TaskInputContext, TaskRunResult
from cdw.infrastructure.task_repository import TaskRepository
from cdw.infrastructure.metadata_database import MetadataDatabase
from cdw.infrastructure.task_runner import TableTaskRunner


class TaskService:
    def __init__(self, config: AppConfig, *, database: MetadataDatabase | None = None):
        self.config = config
        self._repository = TaskRepository(database if database is not None else MetadataDatabase(config.metadata_database))
        self._runner = TableTaskRunner()

    def list_tasks(self) -> list[TaskDefinition]:
        return self._repository.list_tasks()

    def get_task(self, task_id: str) -> TaskDefinition:
        return self._repository.get_task(task_id)

    def save_task(self, task: TaskDefinition) -> TaskDefinition:
        return self._repository.save_task(task)

    def delete_task(self, task_id: str):
        self._repository.delete_task(task_id)

    def run_task(self, task_id: str, result: QueryResult | None = None,
                 cancel: threading.Event | None = None, *, context: TaskInputContext | None = None) -> TaskRunResult:
        return self.run_definition(self.get_task(task_id), result, cancel, context=context)

    def run_definition(self, task: TaskDefinition, result: QueryResult | None = None,
                       cancel: threading.Event | None = None, *, context: TaskInputContext | None = None) -> TaskRunResult:
        """Run outside the DB worker; input lasts until success, failure or cancellation."""
        return self._runner.run(task, result, cancel, context=context)
