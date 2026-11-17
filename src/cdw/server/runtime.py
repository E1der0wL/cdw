"""Headless execution owner: metadata, memory tables, scheduling and jobs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import os
import threading
import time
from uuid import uuid4

from cdw.application.service import ApplicationService
from cdw.application.tasks import TaskService
from cdw.application.scheduling import SchedulingService
from cdw.domain.contracts import CdwError, ErrorCode, RunState
from cdw.domain.execution import JobState, JobView
from cdw.domain.remote import RemoteApi
from cdw.domain.schedules import WorkflowRunState
from cdw.domain.tasks import TaskInputContext, TaskInputFormat
from cdw.domain.workspace import QuerySnapshot
from cdw.infrastructure.database_worker import DatabaseWorker
from cdw.i18n import tr


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _Job:
    view: JobView
    cancel: threading.Event
    session: str


class Runtime:
    JOB_WORKERS = 4
    TICK_SECONDS = 0.25
    RETAINED_JOBS = 128
    MAX_PENDING_JOBS = 64
    SESSION_IDLE_SECONDS = 600
    SESSION_CLEANUP_SECONDS = 30
    CLOSED_SESSION_SECONDS = 60

    def __init__(self, config):
        self.config = config
        self.started_at = utc_now()
        self._mutex = threading.RLock()
        self._resources = set()
        self._jobs = {}
        self._snapshots = {}
        self._closed_sessions = {}
        self._session_seen = {}
        self._last_session_cleanup = time.monotonic()
        self._stopping = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=self.JOB_WORKERS, thread_name_prefix="cdw-execution")
        self.service = ApplicationService(config, store=DatabaseWorker())
        self.tasks = TaskService(config, database=self.service.database)
        self.schedules = SchedulingService(self.service.database, get_preset=self.service.get_preset, get_task=self.tasks.get_task)
        self.service.recover()
        self.schedules.recover_interrupted()
        self._scheduler = threading.Thread(target=self._tick, name="cdw-scheduler", daemon=True)
        self._scheduler.start()

    def status(self):
        with self._mutex:
            return {"running": not self._stopping.is_set(), "pid": os.getpid(), "started_at": self.started_at,
                    "data_directory": str(self.config.data_directory),
                    "active_runs": sum(not job.view.finished for job in self._jobs.values())}

    def dispatch(self, method, arguments, keywords, session):
        if method == "close_session":
            return self.close_session(session)
        self._touch_session(session)
        if method == "heartbeat":
            return None
        if method == "status":
            return self.status()
        if method == "job":
            with self._mutex:
                job = self._jobs[arguments[0]]
                if job.session != session:
                    raise CdwError(ErrorCode.NOT_FOUND)
                return job.view
        if method == "forget_job":
            with self._mutex:
                job = self._jobs.get(arguments[0])
                if job and job.session == session and job.view.finished:
                    del self._jobs[arguments[0]]
            return None
        if method == "cancel":
            return self.cancel(arguments[0], session)
        if self._stopping.is_set():
            raise CdwError(ErrorCode.INTERNAL, tr("server.stopping"))
        if method == "submit":
            return self.submit(arguments[0], arguments[1], arguments[2], session)
        if method == "run_workflow":
            request = self.schedules.create_manual_run(arguments[0])
            try:
                self._launch_workflow(request)
            except Exception as error:
                self.schedules.finish_run(request.id, WorkflowRunState.COLLECTION_FAILED, details=str(error))
                raise
            return request.id
        prefix, _, name = method.partition(".")
        if prefix == "service" and name in RemoteApi.SERVICE_METHODS:
            if name == "release_result":
                self._release_snapshot(arguments[0], session)
                return None
            return getattr(self.service, name)(*arguments, **keywords)
        if prefix == "tasks" and name in RemoteApi.TASK_METHODS:
            return getattr(self.tasks, name)(*arguments, **keywords)
        if prefix == "schedules" and name in RemoteApi.SCHEDULE_METHODS:
            return getattr(self.schedules, name)(*arguments, **keywords)
        raise CdwError(ErrorCode.NOT_FOUND, method)

    def submit(self, operation, arguments, keywords, session):
        if operation not in RemoteApi.JOB_OPERATIONS:
            raise CdwError(ErrorCode.NOT_FOUND, operation)
        identifier = str(uuid4())
        job = _Job(JobView(identifier, operation, JobState.QUEUED, utc_now()), threading.Event(), session)
        with self._mutex:
            self._prune_jobs()
            if sum(not item.view.finished for item in self._jobs.values()) >= self.MAX_PENDING_JOBS:
                raise CdwError(ErrorCode.INTERNAL, tr("server.queue_full"))
            self._jobs[identifier] = job
        self._pool.submit(self._execute, job, arguments, keywords)
        return identifier

    def _progress(self, job, progress):
        with self._mutex:
            job.view = replace(job.view, progress=progress)

    def _finish(self, job, result=None, error=None):
        release = ()
        with self._mutex:
            if isinstance(result, QuerySnapshot):
                self._snapshots.setdefault(job.session, set()).add(result.id)
            code = error.code if isinstance(error, CdwError) else ErrorCode.INTERNAL
            job.view = replace(job.view, state=(JobState.SUCCEEDED if error is None else
                JobState.CANCELLED if code is ErrorCode.CANCELLED else JobState.FAILED), finished_at=utc_now(),
                result=result, error_code=code.value if error else None,
                error_details=error.details if isinstance(error, CdwError) else str(error) if error else "")
            release = self._closed_session_snapshots(job.session)
        for identifier in release:
            self.service.release_result(identifier)

    def _execute(self, job, arguments, keywords):
        resource = None
        try:
            with self._mutex:
                job.view = replace(job.view, state=JobState.RUNNING)
            if job.cancel.is_set():
                raise CdwError(ErrorCode.CANCELLED)
            operation = job.view.operation
            if operation == "collect":
                preset = self.service.get_preset(arguments[0])
                resource = self._reserve_table(preset)
                result = self.service.collect_definition(preset, job.cancel, lambda progress: self._progress(job, progress))
            elif operation == "preview":
                result = self.service.preview(arguments[0], job.cancel)
            elif operation in RemoteApi.QUERY_OPERATIONS:
                result = getattr(self.service, operation)(*arguments, cancel=job.cancel, **keywords)
            elif operation == "run_task":
                result = self.tasks.run_task(*arguments, cancel=job.cancel, **keywords)
            else:
                result = getattr(self.service, operation)(*arguments, **keywords)
            self._finish(job, result)
        except Exception as error:
            self._finish(job, error=error)
        finally:
            if resource is not None:
                self._release_resource(resource)

    def _reserve_table(self, preset):
        key = ("table", preset.storage.key, preset.table_name)
        with self._mutex:
            if key in self._resources:
                raise CdwError(ErrorCode.INTERNAL, tr("server.table_busy"))
            self._resources.add(key)
        return key

    def _release_resource(self, key):
        with self._mutex:
            self._resources.discard(key)

    def _launch_workflow(self, request):
        workflow = self.schedules.get_workflow(request.workflow_id)
        preset = self.service.get_preset(workflow.preset_id)
        task = self.tasks.get_task(workflow.task_id) if workflow.task_id else None
        workflow_key = ("workflow", workflow.id)
        table_key = ("table", preset.storage.key, preset.table_name)
        with self._mutex:
            if workflow_key in self._resources or table_key in self._resources:
                self.schedules.skipped_overlap(request)
                return
            if sum(not item.view.finished for item in self._jobs.values()) >= self.MAX_PENDING_JOBS:
                raise CdwError(ErrorCode.INTERNAL, tr("server.queue_full"))
            self._resources.update((workflow_key, table_key))
            job = _Job(JobView(request.id, "workflow", JobState.QUEUED, utc_now()), threading.Event(), "scheduler")
            self._jobs[request.id] = job
            self._prune_jobs()
        self._pool.submit(self._execute_workflow, request, preset, task, job, workflow_key, table_key)

    def _execute_workflow(self, request, preset, task, job, workflow_key, table_key):
        snapshot = None
        collection = None
        task_started = False
        try:
            self.schedules.start_run(request)
            with self._mutex:
                job.view = replace(job.view, state=JobState.RUNNING)
            collection = self.service.collect_definition(preset, job.cancel, lambda progress: self._progress(job, progress))
            if task is not None:
                if job.cancel.is_set():
                    raise CdwError(ErrorCode.CANCELLED)
                task_started = True
                if task.input_format is not TaskInputFormat.NONE:
                    snapshot = self.service.collection_snapshot(preset, job.cancel)
                context = TaskInputContext(run_id=request.id, table_name=preset.table_name, collected_at=collection.finished_at)
                with self._mutex:
                    job.view = replace(job.view, progress=None)
                outcome = self.tasks.run_definition(task, snapshot.result if snapshot else None, job.cancel, context=context)
                self.schedules.finish_run(request.id, WorkflowRunState.SUCCEEDED, collection_run_id=collection.run_id,
                                          stdout=outcome.stdout, stderr=outcome.stderr)
            else:
                self.schedules.finish_run(request.id, WorkflowRunState.SUCCEEDED, collection_run_id=collection.run_id,
                                          stderr=collection.stderr)
            self._finish(job, result=request.id)
        except Exception as error:
            state = (WorkflowRunState.CANCELLED if isinstance(error, CdwError) and error.code is ErrorCode.CANCELLED
                     else WorkflowRunState.TASK_FAILED if task_started or collection is not None
                     else WorkflowRunState.COLLECTION_FAILED)
            details = f"{error}\n{error.details}".strip() if isinstance(error, CdwError) else str(error)
            try:
                self.schedules.finish_run(request.id, state, collection_run_id=collection.run_id if collection else None, details=details)
            finally:
                self._finish(job, error=error)
        finally:
            try:
                if snapshot is not None:
                    self.service.release_result(snapshot.id)
            finally:
                self._release_resource(table_key)
                self._release_resource(workflow_key)

    def cancel(self, identifier, session):
        with self._mutex:
            job = self._jobs.get(identifier)
            if job is None or job.session != session or job.view.finished:
                return False
            if job.view.progress is not None and job.view.progress.state is RunState.SAVING:
                return False
            job.cancel.set()
            if job.view.operation in RemoteApi.QUERY_OPERATIONS:
                self.service.interrupt_query(job.cancel)
            return True

    def _release_snapshot(self, identifier, session):
        with self._mutex:
            owned = self._snapshots.get(session, set())
            if identifier not in owned:
                raise CdwError(ErrorCode.NOT_FOUND)
            owned.remove(identifier)
        self.service.release_result(identifier)

    def close_session(self, session):
        with self._mutex:
            self._closed_sessions[session] = time.monotonic()
            self._session_seen.pop(session, None)
            snapshots = self._closed_session_snapshots(session)
        for identifier in snapshots:
            self.service.release_result(identifier)

    def _touch_session(self, session):
        with self._mutex:
            if session in self._closed_sessions:
                raise CdwError(ErrorCode.CANCELLED, tr("server.disconnected"))
            self._session_seen[session] = time.monotonic()

    def _expire_sessions(self):
        now = time.monotonic()
        if now - self._last_session_cleanup < self.SESSION_CLEANUP_SECONDS:
            return
        self._last_session_cleanup = now
        with self._mutex:
            expired = [session for session, seen in self._session_seen.items() if now - seen > self.SESSION_IDLE_SECONDS]
            active = {job.session for job in self._jobs.values() if not job.view.finished}
            for session, closed in tuple(self._closed_sessions.items()):
                if session not in active and now - closed > self.CLOSED_SESSION_SECONDS:
                    del self._closed_sessions[session]
        for session in expired:
            self.close_session(session)

    def _closed_session_snapshots(self, session):
        # Called under the mutex. In-flight copy/export jobs retain their inputs.
        if session not in self._closed_sessions:
            return ()
        if any(job.session == session and not job.view.finished for job in self._jobs.values()):
            return ()
        for job in self._jobs.values():
            if job.session == session:
                job.view = replace(job.view, result=None)
        return self._snapshots.pop(session, ())

    def _prune_jobs(self):
        completed = [identifier for identifier, job in self._jobs.items() if job.view.finished]
        for identifier in completed[:max(0, len(self._jobs) - self.RETAINED_JOBS)]:
            del self._jobs[identifier]

    def _tick(self):
        while not self._stopping.wait(self.TICK_SECONDS):
            try:
                self._expire_sessions()
                for request in self.schedules.due_runs():
                    try:
                        self._launch_workflow(request)
                    except Exception as error:
                        self.schedules.finish_run(request.id, WorkflowRunState.COLLECTION_FAILED, details=str(error))
            except Exception as error:
                # Keep the timer alive; diagnostics are captured in the server log.
                print(tr("server.scheduler_error").format(details=str(error)), flush=True)

    def request_stop(self):
        self._stopping.set()
        with self._mutex:
            for job in self._jobs.values():
                if not job.view.finished:
                    job.cancel.set()
        self.service.interrupt_query()

    def close(self):
        self.request_stop()
        self._scheduler.join()
        self._pool.shutdown(wait=True)
        self.service.close()
