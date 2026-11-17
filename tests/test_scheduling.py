"""Public scheduling contracts using explicit time and a durable metadata file."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from cdw.application.scheduling import SchedulingService
from cdw.domain.contracts import CdwError, ErrorCode
from cdw.domain.schedules import (
    ScheduleDefinition, ScheduleEntry, TriggerKind, TriggerSpec, WorkflowDefinition,
    WorkflowRun, WorkflowRunState,
)
from cdw.infrastructure.metadata_database import MetadataDatabase
from cdw.infrastructure.schedule_clock import ScheduleClock


BASE = datetime(2026, 1, 5, tzinfo=timezone.utc)


def service_at(path):
    presets = {"preset": SimpleNamespace(id="preset")}
    tasks = {"task": SimpleNamespace(id="task")}

    def resolve(values, identity):
        if identity not in values:
            raise CdwError(ErrorCode.NOT_FOUND)
        return values[identity]

    return SchedulingService(MetadataDatabase(path),
        get_preset=lambda identity: resolve(presets, identity), get_task=lambda identity: resolve(tasks, identity))


@pytest.fixture
def service(tmp_path):
    return service_at(tmp_path / "metadata.sqlite3")


def interval_schedule(*, grace=60, enabled=True):
    workflow = WorkflowDefinition("workflow", "Collect then process", "preset", "task")
    trigger = TriggerSpec(TriggerKind.INTERVAL, interval_seconds=60, anchor_at=BASE.isoformat())
    schedule = ScheduleDefinition("schedule", "Every minute", workflow.id, trigger, enabled, grace)
    return workflow, schedule


def test_public_definitions_and_history_roundtrip_without_losing_timezone():
    workflow, schedule = interval_schedule()
    assert WorkflowDefinition.from_dict(workflow.to_dict()) == workflow
    assert ScheduleDefinition.from_dict(schedule.to_dict()) == schedule
    run = WorkflowRun("run", workflow.id, workflow.name, schedule.id, BASE.isoformat(),
                      BASE.isoformat(), (BASE + timedelta(seconds=2)).isoformat(), WorkflowRunState.TASK_FAILED,
                      "collection", "child exited", "output", "error")
    entry = ScheduleEntry(schedule, (BASE + timedelta(minutes=1)).isoformat(), run)
    assert WorkflowRun.from_dict(run.to_dict()) == run
    assert ScheduleEntry.from_dict(entry.to_dict()) == entry


@pytest.mark.parametrize("interval", [0, -1, True, float("inf"), float("nan"), TriggerSpec.MAX_INTERVAL_SECONDS + 1])
def test_invalid_intervals_are_rejected(interval):
    with pytest.raises(ValueError):
        TriggerSpec(TriggerKind.INTERVAL, interval_seconds=interval, anchor_at=BASE.isoformat())


@pytest.mark.parametrize("weekdays", [(), (7,), (-1,), (True,), ("1",)])
def test_invalid_weekday_selection_is_rejected(weekdays):
    with pytest.raises(ValueError):
        TriggerSpec(TriggerKind.WEEKLY, wall_time="09:00", weekdays=weekdays)


def test_naive_and_incompatible_trigger_fields_are_rejected():
    with pytest.raises(ValueError):
        TriggerSpec(TriggerKind.ONCE, at="2026-01-05T09:00:00")
    with pytest.raises(ValueError):
        TriggerSpec(TriggerKind.INTERVAL, anchor_at=BASE.isoformat(), interval_seconds=60, at=BASE.isoformat())
    with pytest.raises(ValueError):
        TriggerSpec(TriggerKind.WEEKLY, wall_time="09:00:00+09:00")
    with pytest.raises(ValueError):
        WorkflowDefinition.from_dict({"version": True, "id": "w", "name": "name", "preset_id": "preset", "task_id": None})


def test_save_validates_workflow_references_and_is_atomic(service):
    workflow, schedule = interval_schedule()
    with pytest.raises(CdwError) as missing:
        service.save_bundle(replace(workflow, preset_id="missing"), schedule, now=BASE)
    assert missing.value.code is ErrorCode.NOT_FOUND
    assert service.list_workflows() == []
    assert service.list_schedules() == []
    service.save_bundle(workflow, schedule, now=BASE)
    other = replace(schedule, id="second", workflow_id="second")
    with pytest.raises(CdwError):
        service.save_bundle(replace(workflow, id="second", name="other workflow"), other, now=BASE)
    assert service.list_workflows() == [workflow]


def test_interval_remains_anchored_and_claims_one_latest_missed_occurrence(service, tmp_path):
    workflow, schedule = interval_schedule(grace=10)
    service.save_bundle(workflow, schedule, now=BASE - timedelta(seconds=1))
    now = BASE + timedelta(minutes=6, seconds=5)
    requests = service.due_runs(now)
    assert len(requests) == 1
    assert requests[0].scheduled_at == (BASE + timedelta(minutes=6)).isoformat()
    assert service.get_schedule(schedule.id).next_run_at == (BASE + timedelta(minutes=7)).isoformat()
    assert service.get_run(requests[0].id).state is WorkflowRunState.QUEUED
    assert service.due_runs(now) == []
    reconnected = service_at(tmp_path / "metadata.sqlite3")
    assert reconnected.get_schedule(schedule.id).next_run_at == (BASE + timedelta(minutes=7)).isoformat()
    assert reconnected.due_runs(now) == []


def test_stale_occurrence_is_skipped_and_not_retried(service):
    workflow, schedule = interval_schedule(grace=3)
    service.save_bundle(workflow, schedule, now=BASE)
    now = BASE + timedelta(minutes=4, seconds=5)
    assert service.due_runs(now) == []
    runs = service.list_runs()
    assert len(runs) == 1
    assert runs[0].state is WorkflowRunState.SKIPPED_MISFIRE
    assert runs[0].scheduled_at == (BASE + timedelta(minutes=4)).isoformat()
    assert service.due_runs(now + timedelta(seconds=1)) == []
    assert len(service.list_runs()) == 1
    assert len(service.due_runs(BASE + timedelta(minutes=5))) == 1


def test_pause_resume_chooses_future_anchor_without_replaying_paused_time(service):
    workflow, schedule = interval_schedule()
    service.save_bundle(workflow, schedule, now=BASE)
    paused = service.set_schedule_enabled(schedule.id, False, now=BASE)
    assert not paused.definition.enabled and paused.next_run_at is None
    later = BASE + timedelta(hours=3, seconds=5)
    assert service.due_runs(later) == []
    resumed = service.set_schedule_enabled(schedule.id, True, now=later)
    assert resumed.next_run_at == (BASE + timedelta(hours=3, minutes=1)).isoformat()


def test_one_off_finishes_its_cursor_and_does_not_repeat(service):
    workflow, schedule = interval_schedule()
    trigger = TriggerSpec(TriggerKind.ONCE, at=BASE.isoformat())
    schedule = replace(schedule, trigger=trigger)
    service.save_bundle(workflow, schedule, now=BASE - timedelta(seconds=1))
    request, = service.due_runs(BASE)
    assert service.get_schedule(schedule.id).next_run_at is None
    service.start_run(request, now=BASE)
    completed = service.finish_run(request.id, WorkflowRunState.SUCCEEDED,
                                   collection_run_id="collection", now=BASE + timedelta(seconds=1))
    assert completed.collection_run_id == "collection"
    assert service.get_schedule(schedule.id).last_run == completed
    assert service.due_runs(BASE + timedelta(days=1)) == []
    with pytest.raises(ValueError):
        service.set_schedule_enabled(schedule.id, True, now=BASE + timedelta(days=1))


def test_manual_run_works_for_paused_workflow_and_retains_separate_task_failure(service):
    workflow, schedule = interval_schedule(enabled=False)
    service.save_bundle(workflow, schedule, now=BASE)
    request = service.create_manual_run(workflow.id, now=BASE)
    assert request.schedule_id is None
    assert service.start_run(request, now=BASE).state is WorkflowRunState.RUNNING
    result = service.finish_run(request.id, WorkflowRunState.TASK_FAILED, collection_run_id="successful-collection",
        details="exit 3", stdout="partial output", stderr="child diagnostic", now=BASE + timedelta(seconds=2))
    assert result.state is WorkflowRunState.TASK_FAILED
    assert (result.collection_run_id, result.stdout, result.stderr) == ("successful-collection", "partial output", "child diagnostic")
    assert service.get_schedule(schedule.id).next_run_at is None
    assert service.list_runs(schedule_id=schedule.id) == []
    assert service.list_runs() == [result]


def test_overlap_is_terminal_and_does_not_change_the_next_occurrence(service):
    workflow, schedule = interval_schedule()
    service.save_bundle(workflow, schedule, now=BASE)
    request, = service.due_runs(BASE)
    result = service.skipped_overlap(request, now=BASE)
    assert result.state is WorkflowRunState.SKIPPED_OVERLAP
    assert result.started_at is None
    assert service.get_schedule(schedule.id).next_run_at == (BASE + timedelta(minutes=1)).isoformat()
    with pytest.raises(CdwError):
        service.start_run(request, now=BASE)


def test_restart_marks_unfinished_results_unknown_without_automatic_retry(service, tmp_path):
    workflow, schedule = interval_schedule()
    service.save_bundle(workflow, schedule, now=BASE)
    queued, = service.due_runs(BASE)
    running = service.create_manual_run(workflow.id, now=BASE)
    service.start_run(running, now=BASE)
    complete = service.create_manual_run(workflow.id, now=BASE)
    service.finish_run(complete.id, WorkflowRunState.COLLECTION_FAILED, details="command failed", now=BASE)
    restarted = service_at(tmp_path / "metadata.sqlite3")
    assert restarted.recover_interrupted(now=BASE + timedelta(seconds=10)) == 2
    assert restarted.get_run(queued.id).state is WorkflowRunState.INTERRUPTED
    assert restarted.get_run(running.id).state is WorkflowRunState.INTERRUPTED
    assert restarted.get_run(complete.id).state is WorkflowRunState.COLLECTION_FAILED
    assert restarted.recover_interrupted(now=BASE + timedelta(seconds=11)) == 0
    assert restarted.due_runs(BASE + timedelta(seconds=11)) == []


def test_schedule_deletion_preserves_auditable_run_history(service):
    workflow, schedule = interval_schedule()
    service.save_bundle(workflow, schedule, now=BASE)
    request, = service.due_runs(BASE)
    service.finish_run(request.id, WorkflowRunState.SUCCEEDED, now=BASE)
    with pytest.raises(CdwError):
        service.delete_workflow(workflow.id)
    service.delete_schedule(schedule.id)
    service.delete_workflow(workflow.id)
    assert service.list_schedules() == []
    assert service.list_workflows() == []
    assert service.list_runs()[0].workflow_name == workflow.name


def test_editor_deletion_removes_only_unused_workflow_and_allows_name_reuse(service):
    workflow, schedule = interval_schedule()
    service.save_bundle(workflow, schedule, now=BASE)
    shared = replace(schedule, id="shared", name="Second use")
    service.save_schedule(shared, now=BASE)
    service.delete_schedule(schedule.id, delete_unused_workflow=True)
    assert service.get_workflow(workflow.id) == workflow
    service.delete_schedule(shared.id, delete_unused_workflow=True)
    assert service.list_workflows() == []
    service.save_bundle(replace(workflow, id="replacement"),
                        replace(schedule, id="replacement", workflow_id="replacement"), now=BASE)
    assert len(service.list_schedules()) == 1


def test_weekly_recurrence_uses_selected_days_and_seoul_wall_time():
    trigger = TriggerSpec(TriggerKind.WEEKLY, wall_time="09:30:00", weekdays=(0, 2))
    assert ScheduleClock.next_after(trigger, BASE) == BASE + timedelta(minutes=30)
    monday_run = BASE + timedelta(minutes=30)
    assert ScheduleClock.next_after(trigger, monday_run) == monday_run + timedelta(days=2)
    assert ScheduleClock.next_after(trigger, monday_run, inclusive=True) == monday_run
    assert ScheduleClock.latest_due(trigger, monday_run, BASE + timedelta(days=3)) == monday_run + timedelta(days=2)


def test_dst_recurrence_skips_missing_wall_time_and_uses_repeated_time_once():
    spring = TriggerSpec(TriggerKind.WEEKLY, timezone="America/New_York", wall_time="02:30:00", weekdays=(6,))
    before_gap = datetime(2026, 3, 8, 0, tzinfo=timezone.utc)
    assert ScheduleClock.next_after(spring, before_gap) == datetime(2026, 3, 15, 6, 30, tzinfo=timezone.utc)
    autumn = TriggerSpec(TriggerKind.WEEKLY, timezone="America/New_York", wall_time="01:30:00", weekdays=(6,))
    first = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)
    assert ScheduleClock.next_after(autumn, first - timedelta(seconds=1)) == first
    assert ScheduleClock.next_after(autumn, first) == datetime(2026, 11, 8, 6, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("local", ["2026-03-08 02:30:00", "2026-11-01 01:30:00"])
def test_explicit_ambiguous_or_missing_local_times_require_user_correction(local):
    with pytest.raises(ValueError):
        ScheduleClock.local_datetime(local, "America/New_York")
