"""In-process diagnostics for asyncio jobs; separate from business job state."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import time
import traceback
from dataclasses import dataclass, field
from typing import Iterator

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class TaskTrace:
    job_id: str
    task: asyncio.Task | None
    status: str = "pending"
    created_at: str = field(default_factory=_utc_now)
    created_monotonic: float = field(default_factory=time.monotonic)
    started_monotonic: float | None = None
    current_stage: str | None = None
    stage_started_monotonic: float | None = None
    completed_at: str | None = None
    completed_monotonic: float | None = None
    exception_type: str | None = None
    exception_message: str | None = None
    exception_traceback: str | None = None
    events: list[dict] = field(default_factory=list)


class TaskRegistry:
    """Track execution tasks and their milestones without changing JobManager state."""

    def __init__(self) -> None:
        self._traces: dict[str, TaskTrace] = {}

    def register(self, job_id: str, task: asyncio.Task) -> None:
        trace = TaskTrace(job_id=job_id, task=task)
        self._traces[job_id] = trace
        self._log(trace, "task_registered", "pending")

    def mark_running(self, job_id: str) -> None:
        trace = self._require(job_id)
        trace.status = "running"
        trace.started_monotonic = time.monotonic()
        self._log(trace, "task_started", "running")

    @contextmanager
    def stage(self, job_id: str, name: str) -> Iterator[None]:
        self.begin_stage(job_id, name)
        try:
            yield
        except BaseException as exc:
            trace = self._require(job_id)
            self._store_exception(trace, exc)
            self.end_stage(
                job_id, "cancelled" if isinstance(exc, asyncio.CancelledError) else "error",
                error_type=type(exc).__name__, error=str(exc),
                traceback=self._format_traceback(exc),
            )
            raise
        else:
            self.end_stage(job_id, "completed")

    def begin_stage(self, job_id: str, name: str) -> None:
        trace = self._require(job_id)
        trace.current_stage = name
        trace.stage_started_monotonic = time.monotonic()
        self._log(trace, name, "started")

    def end_stage(self, job_id: str, result: str, **details: object) -> None:
        trace = self._require(job_id)
        started = trace.stage_started_monotonic
        self._log(
            trace, trace.current_stage or "unknown", result,
            duration_ms=None if started is None else (time.monotonic() - started) * 1000,
            **details,
        )
        trace.current_stage = None
        trace.stage_started_monotonic = None

    def finish(self, job_id: str, task: asyncio.Task) -> BaseException | None:
        trace = self._require(job_id)
        exception: BaseException | None = None
        if task.cancelled():
            trace.status = "cancelled"
        else:
            try:
                exception = task.exception()
            except asyncio.CancelledError as exc:
                exception = exc
            if exception is not None:
                trace.status = "exception"
                self._store_exception(trace, exception)
            else:
                trace.status = "completed"
        trace.completed_at = _utc_now()
        trace.completed_monotonic = time.monotonic()
        self._log(
            trace, "task_finished", trace.status,
            duration_ms=(time.monotonic() - trace.created_monotonic) * 1000,
            error_type=trace.exception_type, error=trace.exception_message,
        )
        # Retain the trace and milestone evidence, release only the finished Task.
        trace.task = None
        return exception

    def record_error(self, job_id: str, exc: BaseException, stage: str | None = None) -> None:
        trace = self._require(job_id)
        resolved_stage = stage or trace.current_stage or "unknown"
        self._store_exception(trace, exc)
        if trace.current_stage == resolved_stage and trace.stage_started_monotonic is not None:
            self.end_stage(
                job_id, "cancelled" if isinstance(exc, asyncio.CancelledError) else "error",
                error_type=type(exc).__name__, error=str(exc),
                traceback=self._format_traceback(exc),
            )
        else:
            self._log(
                trace, resolved_stage, "error", error_type=type(exc).__name__,
                error=str(exc), traceback=self._format_traceback(exc),
            )

    def record_event(self, job_id: str, stage: str, result: str, **details: object) -> None:
        self._log(self._require(job_id), stage, result, **details)

    def get(self, job_id: str, *, include_stack: bool = False) -> dict | None:
        trace = self._traces.get(job_id)
        if trace is None:
            return None
        now = time.monotonic()
        elapsed_start = trace.started_monotonic or trace.created_monotonic
        task_status = trace.status
        exception_type, exception_message = trace.exception_type, trace.exception_message
        if trace.task is not None and trace.task.done():
            if trace.task.cancelled():
                task_status = "cancelled"
            else:
                try:
                    task_exception = trace.task.exception()
                except asyncio.CancelledError as exc:
                    task_exception = exc
                if task_exception is not None:
                    task_status = "exception"
                    self._store_exception(trace, task_exception)
                    exception_type, exception_message = trace.exception_type, trace.exception_message
                else:
                    task_status = "completed"
        data = {
            "job_id": trace.job_id,
            "task_status": task_status,
            "current_stage": trace.current_stage,
            "duration_ms": round((now - elapsed_start) * 1000, 2)
            if trace.completed_monotonic is None else round((trace.completed_monotonic - elapsed_start) * 1000, 2),
            "created_at": trace.created_at,
            "completed_at": trace.completed_at,
            "exception": None if exception_type is None else {
                "type": exception_type, "message": exception_message,
                "traceback": trace.exception_traceback,
            },
            "events": [dict(event) for event in trace.events],
        }
        if include_stack:
            task = trace.task
            data["stack"] = [] if task is None else [
                {"file": frame.f_code.co_filename, "line": frame.f_lineno,
                 "function": frame.f_code.co_name}
                for frame in task.get_stack(limit=20)
            ]
        return data

    def _require(self, job_id: str) -> TaskTrace:
        try:
            return self._traces[job_id]
        except KeyError as exc:
            raise KeyError(f"Unknown task trace: {job_id}") from exc

    @staticmethod
    def _format_traceback(exc: BaseException) -> str:
        """Format frames and chained exceptions without locals or call arguments."""
        return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__, chain=True))

    def _store_exception(self, trace: TaskTrace, exc: BaseException) -> None:
        trace.exception_type = type(exc).__name__
        trace.exception_message = str(exc)
        trace.exception_traceback = self._format_traceback(exc)

    def _log(self, trace: TaskTrace, stage: str, result: str, **details: object) -> None:
        now = time.monotonic()
        event = {
            "job_id": trace.job_id,
            "timestamp": _utc_now(),
            "stage": stage,
            "result": result,
            **{key: value for key, value in details.items() if value is not None},
        }
        if trace.current_stage == stage and trace.stage_started_monotonic is not None:
            event.setdefault("stage_elapsed_ms", round((now - trace.stage_started_monotonic) * 1000, 2))
        trace.events.append(event)
        encoded = json.dumps(event, ensure_ascii=False, default=str)
        if result in {"error", "exception"}:
            logger.error("export_trace %s", encoded)
        elif result == "cancelled":
            logger.warning("export_trace %s", encoded)
        else:
            logger.info("export_trace %s", encoded)
