"""Schedule definitions, occurrence cursors and workflow history in SQLite."""

from __future__ import annotations

from datetime import timezone
import json
from typing import TYPE_CHECKING
from uuid import uuid4

from cdw.domain.contracts import CdwError, ErrorCode
from cdw.domain.schedules import (
    RunRequest, ScheduleDefinition, ScheduleEntry, WorkflowDefinition, WorkflowRun,
    WorkflowRunState, aware_datetime,
)
from cdw.infrastructure.schedule_clock import ScheduleClock
from cdw.i18n import tr

if TYPE_CHECKING:
    from cdw.infrastructure.metadata_database import MetadataDatabase


class ScheduleRepository:
    SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS workflows (
        id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE, definition TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS schedules (
        id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE, workflow_id TEXT NOT NULL,
        definition TEXT NOT NULL, next_run_at TEXT
    );
    CREATE INDEX IF NOT EXISTS schedules_due ON schedules(next_run_at);
    CREATE TABLE IF NOT EXISTS workflow_runs (
        id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, workflow_name TEXT NOT NULL,
        schedule_id TEXT, scheduled_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
        state TEXT NOT NULL, collection_run_id TEXT, details TEXT NOT NULL DEFAULT '',
        stdout TEXT NOT NULL DEFAULT '', stderr TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS workflow_runs_time ON workflow_runs(scheduled_at DESC);
    CREATE INDEX IF NOT EXISTS workflow_runs_schedule ON workflow_runs(schedule_id, scheduled_at DESC);
    """
    BEGIN_WRITE_SQL = "BEGIN IMMEDIATE"
    SAVE_WORKFLOW_SQL = """INSERT INTO workflows VALUES (?,?,?) ON CONFLICT(id)
        DO UPDATE SET name=excluded.name, definition=excluded.definition"""
    SAVE_SCHEDULE_SQL = """INSERT INTO schedules VALUES (?,?,?,?,?) ON CONFLICT(id)
        DO UPDATE SET name=excluded.name, workflow_id=excluded.workflow_id,
        definition=excluded.definition, next_run_at=excluded.next_run_at"""
    GET_WORKFLOW_SQL = "SELECT definition FROM workflows WHERE id=?"
    LIST_WORKFLOWS_SQL = "SELECT definition FROM workflows ORDER BY name COLLATE NOCASE"
    DELETE_WORKFLOW_SQL = "DELETE FROM workflows WHERE id=?"
    WORKFLOW_REFERENCED_SQL = "SELECT EXISTS(SELECT 1 FROM schedules WHERE workflow_id=?)"
    GET_SCHEDULE_SQL = "SELECT * FROM schedules WHERE id=?"
    LIST_SCHEDULES_SQL = "SELECT * FROM schedules ORDER BY name COLLATE NOCASE"
    DELETE_SCHEDULE_SQL = "DELETE FROM schedules WHERE id=?"
    SCHEDULE_WORKFLOW_SQL = "SELECT workflow_id FROM schedules WHERE id=?"
    DUE_SQL = "SELECT * FROM schedules WHERE next_run_at IS NOT NULL AND next_run_at<=? ORDER BY next_run_at,id"
    ADVANCE_SQL = "UPDATE schedules SET next_run_at=? WHERE id=?"
    INSERT_RUN_SQL = """INSERT INTO workflow_runs
        (id,workflow_id,workflow_name,schedule_id,scheduled_at,state,finished_at,details)
        VALUES (?,?,?,?,?,?,?,?)"""
    START_RUN_SQL = "UPDATE workflow_runs SET state=?,started_at=? WHERE id=? AND state=?"
    FINISH_RUN_SQL = """UPDATE workflow_runs SET state=?,finished_at=?,collection_run_id=?,details=?,stdout=?,stderr=?
        WHERE id=? AND state IN (?,?)"""
    GET_RUN_SQL = "SELECT * FROM workflow_runs WHERE id=?"
    LIST_RUNS_SQL = "SELECT * FROM workflow_runs ORDER BY scheduled_at DESC,rowid DESC LIMIT ?"
    LIST_SCHEDULE_RUNS_SQL = "SELECT * FROM workflow_runs WHERE schedule_id=? ORDER BY scheduled_at DESC,rowid DESC LIMIT ?"
    LAST_RUN_SQL = "SELECT * FROM workflow_runs WHERE schedule_id=? ORDER BY scheduled_at DESC,rowid DESC LIMIT 1"
    RECOVER_SQL = "UPDATE workflow_runs SET state=?,finished_at=?,details=? WHERE state IN (?,?)"
    MAX_HISTORY_LIMIT = 1000
    DEFAULT_HISTORY_LIMIT = 200

    def __init__(self, database: MetadataDatabase):
        self.database = database

    @staticmethod
    def _json(value):
        return json.dumps(value.to_dict(), ensure_ascii=False)

    @staticmethod
    def _decode(model, value):
        try:
            return model.from_dict(json.loads(value))
        except (ValueError, TypeError, KeyError) as exc:
            raise CdwError(ErrorCode.STORAGE_FAILED, tr("schedule.invalid_saved_definition")) from exc

    @staticmethod
    def _run(row):
        return WorkflowRun.from_dict(dict(row))

    def _entry(self, connection, row):
        latest = connection.execute(self.LAST_RUN_SQL, (row["id"],)).fetchone()
        return ScheduleEntry(self._decode(ScheduleDefinition, row["definition"]), row["next_run_at"],
                             self._run(latest) if latest else None)

    def save_workflow(self, workflow):
        with self.database.connect() as connection:
            connection.execute(self.SAVE_WORKFLOW_SQL, (workflow.id, workflow.name, self._json(workflow)))
        return workflow

    def save_schedule(self, schedule, next_run_at):
        with self.database.connect() as connection:
            connection.execute(self.SAVE_SCHEDULE_SQL, (schedule.id, schedule.name, schedule.workflow_id,
                                                       self._json(schedule), next_run_at))
        return self.get_schedule(schedule.id)

    def save_bundle(self, workflow, schedule, next_run_at):
        with self.database.connect() as connection:
            connection.execute(self.BEGIN_WRITE_SQL)
            connection.execute(self.SAVE_WORKFLOW_SQL, (workflow.id, workflow.name, self._json(workflow)))
            connection.execute(self.SAVE_SCHEDULE_SQL, (schedule.id, schedule.name, schedule.workflow_id,
                                                       self._json(schedule), next_run_at))
        return self.get_schedule(schedule.id)

    def list_workflows(self):
        with self.database.connect() as connection:
            return [self._decode(WorkflowDefinition, row[0]) for row in connection.execute(self.LIST_WORKFLOWS_SQL)]

    def get_workflow(self, workflow_id):
        with self.database.connect() as connection:
            row = connection.execute(self.GET_WORKFLOW_SQL, (workflow_id,)).fetchone()
        if row is None:
            raise CdwError(ErrorCode.NOT_FOUND, tr("schedule.workflow_missing"))
        return self._decode(WorkflowDefinition, row[0])

    def delete_workflow(self, workflow_id):
        with self.database.connect() as connection:
            connection.execute(self.BEGIN_WRITE_SQL)
            if connection.execute(self.WORKFLOW_REFERENCED_SQL, (workflow_id,)).fetchone()[0]:
                raise CdwError(ErrorCode.STORAGE_FAILED, tr("schedule.workflow_in_use"))
            connection.execute(self.DELETE_WORKFLOW_SQL, (workflow_id,))

    def list_schedules(self):
        with self.database.connect() as connection:
            return [self._entry(connection, row) for row in connection.execute(self.LIST_SCHEDULES_SQL).fetchall()]

    def get_schedule(self, schedule_id):
        with self.database.connect() as connection:
            row = connection.execute(self.GET_SCHEDULE_SQL, (schedule_id,)).fetchone()
            if row is None:
                raise CdwError(ErrorCode.NOT_FOUND, tr("schedule.schedule_missing"))
            return self._entry(connection, row)

    def delete_schedule(self, schedule_id, *, delete_unused_workflow=False):
        with self.database.connect() as connection:
            connection.execute(self.BEGIN_WRITE_SQL)
            row = connection.execute(self.SCHEDULE_WORKFLOW_SQL, (schedule_id,)).fetchone()
            connection.execute(self.DELETE_SCHEDULE_SQL, (schedule_id,))
            if row is not None and delete_unused_workflow:
                workflow_id = row["workflow_id"]
                if not connection.execute(self.WORKFLOW_REFERENCED_SQL, (workflow_id,)).fetchone()[0]:
                    connection.execute(self.DELETE_WORKFLOW_SQL, (workflow_id,))

    def _insert_request(self, connection, request, *, state=WorkflowRunState.QUEUED, finished_at=None, details=""):
        row = connection.execute(self.GET_WORKFLOW_SQL, (request.workflow_id,)).fetchone()
        if row is None:
            raise CdwError(ErrorCode.NOT_FOUND, tr("schedule.workflow_missing"))
        workflow = self._decode(WorkflowDefinition, row[0])
        connection.execute(self.INSERT_RUN_SQL, (request.id, workflow.id, workflow.name,
                                               request.schedule_id, request.scheduled_at,
                                               state.value, finished_at, details))

    def claim_due(self, now):
        timestamp = now.astimezone(timezone.utc).isoformat()
        requests = []
        with self.database.connect() as connection:
            connection.execute(self.BEGIN_WRITE_SQL)
            for row in connection.execute(self.DUE_SQL, (timestamp,)).fetchall():
                schedule = self._decode(ScheduleDefinition, row["definition"])
                if not schedule.enabled:
                    connection.execute(self.ADVANCE_SQL, (None, schedule.id))
                    continue
                due = ScheduleClock.latest_due(schedule.trigger, aware_datetime(row["next_run_at"]), now)
                if due is None:
                    continue
                following = ScheduleClock.next_after(schedule.trigger, now)
                connection.execute(self.ADVANCE_SQL, (following.isoformat() if following else None, schedule.id))
                request = RunRequest(str(uuid4()), schedule.workflow_id, schedule.id, due.isoformat())
                if (now - due).total_seconds() <= schedule.misfire_grace_seconds:
                    self._insert_request(connection, request)
                    requests.append(request)
                else:
                    self._insert_request(connection, request, state=WorkflowRunState.SKIPPED_MISFIRE,
                                         finished_at=timestamp, details=tr("schedule.misfire_details"))
        return requests

    def create_manual_run(self, workflow_id, now):
        request = RunRequest(str(uuid4()), workflow_id, None, now.astimezone(timezone.utc).isoformat())
        with self.database.connect() as connection:
            self._insert_request(connection, request)
        return request

    def start_run(self, request, now):
        with self.database.connect() as connection:
            changed = connection.execute(self.START_RUN_SQL, (WorkflowRunState.RUNNING.value,
                now.astimezone(timezone.utc).isoformat(), request.id, WorkflowRunState.QUEUED.value)).rowcount
            if not changed:
                raise CdwError(ErrorCode.STORAGE_FAILED, tr("schedule.run_already_started"))
        return self.get_run(request.id)

    def finish_run(self, run_id, state, *, collection_run_id=None, details="", stdout="", stderr="", now):
        if not isinstance(state, WorkflowRunState) or state in (WorkflowRunState.QUEUED, WorkflowRunState.RUNNING):
            raise ValueError(tr("schedule.invalid_run_state"))
        with self.database.connect() as connection:
            connection.execute(self.FINISH_RUN_SQL, (state.value, now.astimezone(timezone.utc).isoformat(),
                collection_run_id, details, stdout, stderr, run_id,
                WorkflowRunState.QUEUED.value, WorkflowRunState.RUNNING.value))
        return self.get_run(run_id)

    def get_run(self, run_id):
        with self.database.connect() as connection:
            row = connection.execute(self.GET_RUN_SQL, (run_id,)).fetchone()
        if row is None:
            raise CdwError(ErrorCode.NOT_FOUND, tr("schedule.run_missing"))
        return self._run(row)

    def list_runs(self, limit=DEFAULT_HISTORY_LIMIT, schedule_id=None):
        if type(limit) is not int or not 1 <= limit <= self.MAX_HISTORY_LIMIT:
            raise ValueError(tr("schedule.invalid_history_limit"))
        with self.database.connect() as connection:
            rows = (connection.execute(self.LIST_RUNS_SQL, (limit,)) if schedule_id is None
                    else connection.execute(self.LIST_SCHEDULE_RUNS_SQL, (schedule_id, limit)))
            return [self._run(row) for row in rows]

    def recover_interrupted(self, now):
        with self.database.connect() as connection:
            return connection.execute(self.RECOVER_SQL, (WorkflowRunState.INTERRUPTED.value,
                now.astimezone(timezone.utc).isoformat(), tr("schedule.interrupted_details"),
                WorkflowRunState.QUEUED.value, WorkflowRunState.RUNNING.value)).rowcount
