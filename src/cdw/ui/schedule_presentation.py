"""Shared labels and formatting for the schedule editor and list."""

from cdw.domain.schedules import TriggerKind, TriggerSpec, WorkflowRunState, aware_datetime
from cdw.infrastructure.schedule_clock import ScheduleClock
from cdw.i18n import tr


class SchedulePresentation:
    EMPTY = "—"
    TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
    WEEKDAYS = (tr("schedule.dialog.mon"), tr("schedule.dialog.tue"), tr("schedule.dialog.wed"),
                tr("schedule.dialog.thu"), tr("schedule.dialog.fri"), tr("schedule.dialog.sat"), tr("schedule.dialog.sun"))
    STATES = {
        WorkflowRunState.QUEUED: tr("schedule.ui.queued"),
        WorkflowRunState.RUNNING: tr("run.running"),
        WorkflowRunState.SUCCEEDED: tr("schedule.ui.succeeded"),
        WorkflowRunState.COLLECTION_FAILED: tr("schedule.ui.collection_failed"),
        WorkflowRunState.TASK_FAILED: tr("schedule.ui.task_failed"),
        WorkflowRunState.CANCELLED: tr("common.cancel"),
        WorkflowRunState.INTERRUPTED: tr("schedule.ui.interrupted"),
        WorkflowRunState.SKIPPED_OVERLAP: tr("schedule.ui.skipped_overlap"),
        WorkflowRunState.SKIPPED_MISFIRE: tr("schedule.ui.skipped_misfire"),
    }

    @classmethod
    def timestamp(cls, value, zone_name=TriggerSpec.DEFAULT_TIMEZONE):
        return aware_datetime(value).astimezone(ScheduleClock.zone(zone_name)).strftime(cls.TIME_FORMAT) if value else cls.EMPTY

    @classmethod
    def state(cls, value):
        return cls.STATES[value]

    @classmethod
    def trigger(cls, value):
        if value.kind is TriggerKind.INTERVAL:
            return tr("schedule.ui.interval_description").format(seconds=value.interval_seconds)
        if value.kind is TriggerKind.ONCE:
            return tr("schedule.ui.once_description").format(at=cls.timestamp(value.at, value.timezone))
        days = "·".join(cls.WEEKDAYS[day] for day in value.weekdays)
        return tr("schedule.ui.weekly_description").format(days=days, time=value.wall_time)
