"""Thread-safe in-memory job lifecycle contract for future worker integration."""

from dataclasses import replace
from datetime import datetime, timezone
import math
import threading
from uuid import uuid4

from .models import JobRecord, JobState, TERMINAL_JOB_STATES


class InvalidJobTransition(RuntimeError):
    """Raised when a caller attempts a state change invalid for the job lifecycle."""


class JobManager:
    """Track job state, progress, cooperative cancellation, and structured errors.

    A running cancellation request only sets ``cancel_requested``. A worker must
    stop its operation and then call ``acknowledge_cancel`` before the record is
    marked cancelled. Queued work can be cancelled immediately because it has
    not started. All transitions are checked under one lock.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def create(self, job_type: str, message: str | None = None) -> JobRecord:
        if not job_type.strip():
            raise ValueError("job_type cannot be empty")
        now = self._now()
        record = JobRecord(
            job_id=str(uuid4()), job_type=job_type, state=JobState.QUEUED,
            message=message, created_at=now, updated_at=now,
        )
        with self._lock:
            self._jobs[record.job_id] = record
        return record

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> tuple[JobRecord, ...]:
        with self._lock:
            return tuple(self._jobs.values())

    def start(self, job_id: str, message: str | None = None) -> JobRecord:
        with self._lock:
            record = self._require_locked(job_id)
            if record.state != JobState.QUEUED or record.cancel_requested:
                raise InvalidJobTransition(f"Cannot start job in state {record.state}")
            return self._replace_locked(record, state=JobState.RUNNING, message=message)

    def update_progress(self, job_id: str, progress: float, message: str | None = None) -> JobRecord:
        if not math.isfinite(progress) or not 0 <= progress <= 100:
            raise ValueError("progress must be finite and between 0 and 100")
        with self._lock:
            record = self._require_locked(job_id)
            if record.state != JobState.RUNNING:
                raise InvalidJobTransition(f"Cannot update progress in state {record.state}")
            if progress < record.progress:
                raise ValueError("progress cannot move backwards")
            return self._replace_locked(record, progress=progress, message=message)

    def succeed(self, job_id: str, message: str | None = None) -> JobRecord:
        with self._lock:
            record = self._require_locked(job_id)
            if record.state != JobState.RUNNING:
                raise InvalidJobTransition(f"Cannot succeed job in state {record.state}")
            # Completion is authoritative if the operation finished before it
            # could stop; a mere request must never relabel it as cancelled.
            return self._replace_locked(
                record, state=JobState.SUCCEEDED, progress=100,
                message=message, cancel_requested=False,
            )

    def fail(
        self, job_id: str, error_code: str, error_message: str,
        *, retryable: bool = False,
    ) -> JobRecord:
        if not error_code.strip() or not error_message.strip():
            raise ValueError("error_code and error_message cannot be empty")
        with self._lock:
            record = self._require_locked(job_id)
            if record.state not in (JobState.QUEUED, JobState.RUNNING):
                raise InvalidJobTransition(f"Cannot fail job in state {record.state}")
            return self._replace_locked(
                record, state=JobState.FAILED,
                error_code=error_code, error_message=error_message,
                error_retryable=retryable,
                message="Job failed",
            )

    def request_cancel(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self._require_locked(job_id)
            if record.state == JobState.CANCELLED:
                return record
            if record.state in TERMINAL_JOB_STATES and record.state != JobState.CANCELLED:
                raise InvalidJobTransition(f"Cannot cancel terminal job in state {record.state}")
            if record.state == JobState.QUEUED:
                return self._replace_locked(
                    record, state=JobState.CANCELLED, cancel_requested=True,
                    message="Cancelled before execution",
                )
            return self._replace_locked(
                record, cancel_requested=True, message="Cancellation requested",
            )

    def is_cancel_requested(self, job_id: str) -> bool:
        """Worker polling helper; cancellation remains cooperative."""
        with self._lock:
            return self._require_locked(job_id).cancel_requested

    def acknowledge_cancel(self, job_id: str) -> JobRecord:
        """Mark cancelled only after a worker confirms its operation has stopped."""
        with self._lock:
            record = self._require_locked(job_id)
            if record.state == JobState.CANCELLED:
                return record
            if record.state != JobState.RUNNING or not record.cancel_requested:
                raise InvalidJobTransition(
                    "A worker can acknowledge cancellation only after a running job was asked to stop"
                )
            return self._replace_locked(
                record, state=JobState.CANCELLED, message="Job cancelled",
            )

    def _require_locked(self, job_id: str) -> JobRecord:
        record = self._jobs.get(job_id)
        if record is None:
            raise KeyError(f"Unknown job: {job_id}")
        return record

    def _replace_locked(self, record: JobRecord, **changes: object) -> JobRecord:
        updated = replace(record, updated_at=self._now(), **changes)
        self._jobs[record.job_id] = updated
        return updated
