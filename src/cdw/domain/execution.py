"""Public execution records shared by the local server and its clients."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from cdw.domain.contracts import Progress


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class JobView:
    TERMINAL_STATES = frozenset((JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED))

    id: str
    operation: str
    state: JobState
    created_at: str
    finished_at: str | None = None
    progress: Progress | None = None
    result: Any = None
    error_code: str | None = None
    error_details: str = ""


    @property
    def finished(self) -> bool:
        return self.state in self.TERMINAL_STATES
