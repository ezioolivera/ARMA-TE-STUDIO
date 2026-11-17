"""Typed lifecycle and error contract shared with future background workers."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_JOB_STATES = frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED})


@dataclass(frozen=True, slots=True)
class JobError:
    code: str
    message: str
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    job_type: str
    state: JobState
    progress: float = 0.0
    message: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_retryable: bool = False
    cancel_requested: bool = False
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def error(self) -> JobError | None:
        """Structured view while retaining the original flat record fields."""
        if self.error_code is None or self.error_message is None:
            return None
        return JobError(self.error_code, self.error_message, self.error_retryable)
