"""Scheduling use cases; the server supplies the timer and execution worker."""

from dataclasses import replace
from datetime import datetime, timezone

from cdw.domain.schedules import WorkflowRunState
from cdw.infrastructure.schedule_clock import ScheduleClock
from cdw.infrastructure.schedule_repository import ScheduleRepository
from cdw.i18n import tr


class SchedulingService:
    DEFAULT_HISTORY_LIMIT = ScheduleRepository.DEFAULT_HISTORY_LIMIT

    def __init__(self, database, *, get_preset, get_task):
        self._repository = ScheduleRepository(database)
        self._get_preset = get_preset
        self._get_task = get_task

    @staticmethod
    def _now(now):
        value = now if now is not None else datetime.now(timezone.utc)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(tr("schedule.invalid_datetime"))
        return value.astimezone(timezone.utc)

    def _validate_workflow(self, workflow):
        self._get_preset(workflow.preset_id)
        if workflow.task_id is not None:
            self._get_task(workflow.task_id)

    @staticmethod
    def _next_run(schedule, now):
        ScheduleClock.zone(schedule.trigger.timezone)
        if not schedule.enabled:
            return None
        upcoming = ScheduleClock.next_after(schedule.trigger, now, inclusive=True)
        if upcoming is None:
            raise ValueError(tr("schedule.once_in_past"))
        return upcoming.isoformat()

    def list_workflows(self):
        return self._repository.list_workflows()

    def get_workflow(self, workflow_id):
        return self._repository.get_workflow(workflow_id)

    def save_workflow(self, workflow):
        self._validate_workflow(workflow)
        return self._repository.save_workflow(workflow)

    def delete_workflow(self, workflow_id):
        self._repository.delete_workflow(workflow_id)

    def list_schedules(self):
        return self._repository.list_schedules()

    def get_schedule(self, schedule_id):
        return self._repository.get_schedule(schedule_id)

    def save_schedule(self, schedule, *, now=None):
        self._validate_workflow(self.get_workflow(schedule.workflow_id))
        return self._repository.save_schedule(schedule, self._next_run(schedule, self._now(now)))

    def save_bundle(self, workflow, schedule, *, now=None):
        if workflow.id != schedule.workflow_id:
            raise ValueError(tr("schedule.workflow_mismatch"))
        self._validate_workflow(workflow)
        return self._repository.save_bundle(workflow, schedule, self._next_run(schedule, self._now(now)))

    def set_schedule_enabled(self, schedule_id, enabled, *, now=None):
        entry = self.get_schedule(schedule_id)
        return self.save_schedule(replace(entry.definition, enabled=enabled), now=now)

    def delete_schedule(self, schedule_id, *, delete_unused_workflow=False):
        self._repository.delete_schedule(schedule_id, delete_unused_workflow=delete_unused_workflow)

    def due_runs(self, now=None):
        return self._repository.claim_due(self._now(now))

    def create_manual_run(self, workflow_id, *, now=None):
        self._validate_workflow(self.get_workflow(workflow_id))
        return self._repository.create_manual_run(workflow_id, self._now(now))

    def start_run(self, request, *, now=None):
        return self._repository.start_run(request, self._now(now))

    def finish_run(self, run_id, state, *, collection_run_id=None, details="", stdout="", stderr="", now=None):
        return self._repository.finish_run(run_id, WorkflowRunState(state), collection_run_id=collection_run_id,
            details=details, stdout=stdout, stderr=stderr, now=self._now(now))

    def skipped_overlap(self, request, *, now=None):
        return self.finish_run(request.id, WorkflowRunState.SKIPPED_OVERLAP,
                               details=tr("schedule.overlap_details"), now=now)

    def recover_interrupted(self, *, now=None):
        return self._repository.recover_interrupted(self._now(now))

    def list_runs(self, limit=DEFAULT_HISTORY_LIMIT, schedule_id=None):
        return self._repository.list_runs(limit, schedule_id)

    def get_run(self, run_id):
        return self._repository.get_run(run_id)
