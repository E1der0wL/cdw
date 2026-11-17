"""Persistent scheduling and workflow contracts, independent of GUI and timers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
import math

from cdw.i18n import tr


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(tr("schedule.invalid_identity"))
    return value.strip()


def aware_datetime(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(tr("schedule.invalid_datetime")) from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(tr("schedule.invalid_datetime"))
    return result


class TriggerKind(StrEnum):
    INTERVAL = "interval"
    ONCE = "once"
    WEEKLY = "weekly"


@dataclass(frozen=True)
class TriggerSpec:
    DEFAULT_TIMEZONE = "Asia/Seoul"
    ALL_WEEKDAYS = tuple(range(7))
    MAX_INTERVAL_SECONDS = 365 * 24 * 60 * 60

    kind: TriggerKind
    timezone: str = DEFAULT_TIMEZONE
    interval_seconds: float | None = None
    anchor_at: str | None = None
    at: str | None = None
    wall_time: str | None = None
    weekdays: tuple[int, ...] = ALL_WEEKDAYS

    def __post_init__(self):
        if not isinstance(self.kind, TriggerKind) or not isinstance(self.timezone, str) or not self.timezone:
            raise ValueError(tr("schedule.invalid_trigger"))
        if not isinstance(self.weekdays, (tuple, list)) or not self.weekdays:
            raise ValueError(tr("schedule.invalid_weekdays"))
        if any(type(day) is not int or day not in self.ALL_WEEKDAYS for day in self.weekdays):
            raise ValueError(tr("schedule.invalid_weekdays"))
        if self.kind is TriggerKind.INTERVAL:
            if (isinstance(self.interval_seconds, bool) or not isinstance(self.interval_seconds, (int, float))
                    or not math.isfinite(self.interval_seconds) or not 1 <= self.interval_seconds <= self.MAX_INTERVAL_SECONDS):
                raise ValueError(tr("schedule.invalid_interval"))
            aware_datetime(self.anchor_at)
            if self.at is not None or self.wall_time is not None:
                raise ValueError(tr("schedule.invalid_trigger"))
        elif self.kind is TriggerKind.ONCE:
            aware_datetime(self.at)
            if self.interval_seconds is not None or self.anchor_at is not None or self.wall_time is not None:
                raise ValueError(tr("schedule.invalid_trigger"))
        else:
            try:
                clock = time.fromisoformat(self.wall_time)
            except (TypeError, ValueError) as exc:
                raise ValueError(tr("schedule.invalid_time")) from exc
            if clock.tzinfo is not None or clock.microsecond:
                raise ValueError(tr("schedule.invalid_time"))
            if self.interval_seconds is not None or self.anchor_at is not None or self.at is not None:
                raise ValueError(tr("schedule.invalid_trigger"))
        object.__setattr__(self, "weekdays", tuple(sorted(set(self.weekdays))))

    def to_dict(self):
        return {
            "kind": self.kind.value, "timezone": self.timezone,
            "interval_seconds": self.interval_seconds, "anchor_at": self.anchor_at,
            "at": self.at, "wall_time": self.wall_time, "weekdays": list(self.weekdays),
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"kind", "timezone", "interval_seconds", "anchor_at", "at", "wall_time", "weekdays"}:
            raise ValueError(tr("schedule.invalid_trigger"))
        return cls(**{**value, "kind": TriggerKind(value["kind"])})


@dataclass(frozen=True)
class WorkflowDefinition:
    FORMAT_VERSION = 1

    id: str
    name: str
    preset_id: str
    task_id: str | None = None

    def __post_init__(self):
        for field in ("id", "name", "preset_id"):
            object.__setattr__(self, field, _identifier(getattr(self, field)))
        if self.task_id is not None:
            object.__setattr__(self, "task_id", _identifier(self.task_id))

    def to_dict(self):
        return {"version": self.FORMAT_VERSION, "id": self.id, "name": self.name,
                "preset_id": self.preset_id, "task_id": self.task_id}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, dict) or type(value.get("version")) is not int
                or value["version"] != cls.FORMAT_VERSION
                or set(value) != {"version", "id", "name", "preset_id", "task_id"}):
            raise ValueError(tr("schedule.invalid_workflow"))
        return cls(**{key: item for key, item in value.items() if key != "version"})


@dataclass(frozen=True)
class ScheduleDefinition:
    FORMAT_VERSION = 1
    DEFAULT_GRACE_SECONDS = 60
    MAX_GRACE_SECONDS = 24 * 60 * 60

    id: str
    name: str
    workflow_id: str
    trigger: TriggerSpec
    enabled: bool = True
    misfire_grace_seconds: int = DEFAULT_GRACE_SECONDS

    def __post_init__(self):
        for field in ("id", "name", "workflow_id"):
            object.__setattr__(self, field, _identifier(getattr(self, field)))
        if not isinstance(self.trigger, TriggerSpec) or type(self.enabled) is not bool:
            raise ValueError(tr("schedule.invalid_schedule"))
        if type(self.misfire_grace_seconds) is not int or not 0 <= self.misfire_grace_seconds <= self.MAX_GRACE_SECONDS:
            raise ValueError(tr("schedule.invalid_grace"))

    def to_dict(self):
        return {"version": self.FORMAT_VERSION, "id": self.id, "name": self.name,
                "workflow_id": self.workflow_id, "trigger": self.trigger.to_dict(),
                "enabled": self.enabled, "misfire_grace_seconds": self.misfire_grace_seconds}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, dict) or type(value.get("version")) is not int
                or value["version"] != cls.FORMAT_VERSION
                or set(value) != {"version", "id", "name", "workflow_id", "trigger", "enabled", "misfire_grace_seconds"}):
            raise ValueError(tr("schedule.invalid_schedule"))
        fields = {key: item for key, item in value.items() if key != "version"}
        fields["trigger"] = TriggerSpec.from_dict(fields["trigger"])
        return cls(**fields)


class WorkflowRunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    COLLECTION_FAILED = "collection_failed"
    TASK_FAILED = "task_failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    SKIPPED_OVERLAP = "skipped_overlap"
    SKIPPED_MISFIRE = "skipped_misfire"


@dataclass(frozen=True)
class RunRequest:
    id: str
    workflow_id: str
    schedule_id: str | None
    scheduled_at: str

    def to_dict(self):
        return {"id": self.id, "workflow_id": self.workflow_id,
                "schedule_id": self.schedule_id, "scheduled_at": self.scheduled_at}

    @classmethod
    def from_dict(cls, value):
        return cls(**value)


@dataclass(frozen=True)
class WorkflowRun:
    id: str
    workflow_id: str
    workflow_name: str
    schedule_id: str | None
    scheduled_at: str
    started_at: str | None
    finished_at: str | None
    state: WorkflowRunState
    collection_run_id: str | None = None
    details: str = ""
    stdout: str = ""
    stderr: str = ""

    def to_dict(self):
        return {"id": self.id, "workflow_id": self.workflow_id, "workflow_name": self.workflow_name,
                "schedule_id": self.schedule_id, "scheduled_at": self.scheduled_at,
                "started_at": self.started_at, "finished_at": self.finished_at, "state": self.state.value,
                "collection_run_id": self.collection_run_id, "details": self.details,
                "stdout": self.stdout, "stderr": self.stderr}

    @classmethod
    def from_dict(cls, value):
        return cls(**{**value, "state": WorkflowRunState(value["state"])})


@dataclass(frozen=True)
class ScheduleEntry:
    definition: ScheduleDefinition
    next_run_at: str | None
    last_run: WorkflowRun | None = None

    def to_dict(self):
        return {"definition": self.definition.to_dict(), "next_run_at": self.next_run_at,
                "last_run": self.last_run.to_dict() if self.last_run else None}

    @classmethod
    def from_dict(cls, value):
        return cls(ScheduleDefinition.from_dict(value["definition"]), value["next_run_at"],
                   WorkflowRun.from_dict(value["last_run"]) if value["last_run"] else None)
