"""Debounced, coalescing autosave primitive with observable bounded retries."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from math import isfinite
from types import MappingProxyType
from typing import Any
import copy

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AutosavePolicy:
    enabled: bool = True
    debounce_seconds: float = 1.0
    flush_on_shutdown: bool = True
    retry_attempts: int = 3
    retry_backoff_seconds: float = 0.25
    max_retry_backoff_seconds: float = 5.0

    def __post_init__(self) -> None:
        numeric = (
            self.debounce_seconds,
            self.retry_backoff_seconds,
            self.max_retry_backoff_seconds,
        )
        if any(not isfinite(value) or value < 0 for value in numeric):
            raise ValueError("Autosave delays must be finite and non-negative")
        if self.retry_attempts < 0:
            raise ValueError("retry_attempts cannot be negative")


@dataclass(frozen=True, slots=True)
class AutosaveFailure:
    project_id: str
    revision: int
    attempts: int
    error_type: str
    message: str
    failed_at: datetime


class AutosaveWriteError(RuntimeError):
    """Raised by an explicit flush when the snapshot remains unsaved."""

    def __init__(self, failure: AutosaveFailure) -> None:
        super().__init__(
            f"Autosave for {failure.project_id} revision {failure.revision} "
            f"failed after {failure.attempts} attempts: {failure.message}"
        )
        self.failure = failure


class AutosaveShutdownError(RuntimeError):
    """Aggregates project snapshots that could not be flushed during close."""

    def __init__(self, failures: tuple[AutosaveFailure, ...]) -> None:
        super().__init__(f"Could not flush {len(failures)} project autosave(s) on shutdown")
        self.failures = failures


SnapshotWriter = Callable[[str, Mapping[str, Any]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _PendingSnapshot:
    revision: int
    data: Mapping[str, Any]
    scheduled_at: float


def _freeze(value: Any) -> Any:
    """Recursively copy and freeze JSON-like data owned by the coordinator."""
    if isinstance(value, Mapping):
        return MappingProxyType({copy.deepcopy(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return copy.deepcopy(value)


def _thaw(value: Any) -> Any:
    """Give a writer an isolated mutable copy, never the coordinator snapshot."""
    if isinstance(value, Mapping):
        return {copy.deepcopy(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return {_thaw(item) for item in value}
    return copy.deepcopy(value)


class AutosaveCoordinator:
    """Coalesce immutable snapshots and serialize writes independently per project.

    A project has at most one worker. A newer snapshot never cancels an in-flight
    writer call: once it returns, the worker observes the newer revision and
    writes it. Failed writes are logged, queryable through ``get_error``, and
    retried with bounded exponential backoff. The writer receives a fresh mutable
    copy on every attempt, so mutations or failures cannot change pending data.
    The writer contract is an idempotent full-snapshot replacement; retries are
    not suitable for append-only writers.
    """

    def __init__(self, writer: SnapshotWriter, policy: AutosavePolicy | None = None) -> None:
        self._writer = writer
        self.policy = policy or AutosavePolicy()
        self._pending: dict[str, _PendingSnapshot] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._wake_events: dict[str, asyncio.Event] = {}
        self._force_flush: set[str] = set()
        self._next_revision: dict[str, int] = {}
        self._retry_state: dict[str, tuple[int, int]] = {}
        self._errors: dict[str, AutosaveFailure] = {}
        self._closed = False

    def schedule(self, project_id: str, snapshot: Mapping[str, Any]) -> int | None:
        """Store a deeply isolated snapshot and debounce its write."""
        if not self.policy.enabled:
            return None
        if self._closed:
            raise RuntimeError("AutosaveCoordinator is closed")
        if not isinstance(snapshot, Mapping):
            raise TypeError("snapshot must be a mapping")

        revision = self._next_revision.get(project_id, 0) + 1
        self._next_revision[project_id] = revision
        self._pending[project_id] = _PendingSnapshot(
            revision=revision,
            data=_freeze(snapshot),
            scheduled_at=asyncio.get_running_loop().time(),
        )
        self._retry_state[project_id] = (revision, 0)
        self._wake_events.setdefault(project_id, asyncio.Event()).set()
        self._ensure_worker(project_id)
        return revision

    def get_error(self, project_id: str) -> AutosaveFailure | None:
        """Return the latest unresolved writer failure, if any."""
        return self._errors.get(project_id)

    def has_pending(self, project_id: str) -> bool:
        return project_id in self._pending

    def _ensure_worker(self, project_id: str) -> asyncio.Task[None]:
        task = self._workers.get(project_id)
        if task is None or task.done():
            task = asyncio.create_task(self._run_project(project_id))
            self._workers[project_id] = task
        return task

    async def _wait_until_debounced(self, project_id: str) -> None:
        event = self._wake_events[project_id]
        loop = asyncio.get_running_loop()
        while project_id not in self._force_flush:
            pending = self._pending.get(project_id)
            if pending is None:
                return
            remaining = pending.scheduled_at + self.policy.debounce_seconds - loop.time()
            if remaining <= 0:
                return
            event.clear()
            try:
                await asyncio.wait_for(event.wait(), timeout=remaining)
            except TimeoutError:
                return

    def _record_failure(
        self, project_id: str, revision: int, attempts: int, exc: Exception
    ) -> AutosaveFailure:
        failure = AutosaveFailure(
            project_id=project_id,
            revision=revision,
            attempts=attempts,
            error_type=type(exc).__name__,
            message=str(exc) or type(exc).__name__,
            failed_at=datetime.now(timezone.utc),
        )
        self._errors[project_id] = failure
        logger.error(
            "Autosave failed for project %s, revision %s, attempt %s: %s",
            project_id, revision, attempts, failure.message,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return failure

    async def _run_project(self, project_id: str) -> None:
        task = asyncio.current_task()
        event = self._wake_events[project_id]
        try:
            while True:
                pending = self._pending.get(project_id)
                if pending is None:
                    self._force_flush.discard(project_id)
                    return

                await self._wait_until_debounced(project_id)
                pending = self._pending.get(project_id)
                if pending is None:
                    continue

                state_revision, retries_used = self._retry_state.get(
                    project_id, (pending.revision, 0)
                )
                if state_revision != pending.revision:
                    retries_used = 0
                    self._retry_state[project_id] = (pending.revision, 0)
                attempt = retries_used + 1

                try:
                    await self._writer(project_id, _thaw(pending.data))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    latest = self._pending.get(project_id)
                    self._record_failure(project_id, pending.revision, attempt, exc)
                    if latest is None:
                        continue
                    if latest.revision != pending.revision:
                        self._retry_state[project_id] = (latest.revision, 0)
                        continue

                    if retries_used >= self.policy.retry_attempts:
                        # Keep the immutable snapshot for an explicit later flush
                        # or a new edit; expose the failure instead of dropping it.
                        self._retry_state[project_id] = (pending.revision, retries_used)
                        self._force_flush.discard(project_id)
                        return

                    next_retry = retries_used + 1
                    self._retry_state[project_id] = (pending.revision, next_retry)
                    delay = min(
                        self.policy.retry_backoff_seconds * (2 ** retries_used),
                        self.policy.max_retry_backoff_seconds,
                    )
                    logger.warning(
                        "Autosave for project %s will retry revision %s in %.3fs "
                        "(%s retries remain)",
                        project_id, pending.revision, delay,
                        self.policy.retry_attempts - retries_used,
                    )
                    event.clear()
                    try:
                        await asyncio.wait_for(event.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                    # A fresh revision gets its own retry budget and debounce.
                    latest = self._pending.get(project_id)
                    if latest is not None and latest.revision != pending.revision:
                        self._retry_state[project_id] = (latest.revision, 0)
                    continue

                latest = self._pending.get(project_id)
                if latest is not None and latest.revision == pending.revision:
                    self._pending.pop(project_id, None)
                    self._retry_state.pop(project_id, None)
                    self._errors.pop(project_id, None)
                elif latest is not None:
                    self._retry_state[project_id] = (latest.revision, 0)
                    # The latest snapshot retains its own debounce deadline.
                    continue
        finally:
            if self._workers.get(project_id) is task:
                self._workers.pop(project_id, None)
            if project_id not in self._pending:
                self._force_flush.discard(project_id)
                self._wake_events.pop(project_id, None)

    async def flush(self, project_id: str) -> None:
        """Write the latest snapshot immediately or raise its final failure."""
        while project_id in self._pending:
            revision = self._pending[project_id].revision
            self._force_flush.add(project_id)
            # An explicit flush is an explicit retry opportunity after exhaustion.
            previous = self._retry_state.get(project_id)
            if previous is not None and previous[0] == revision:
                self._retry_state[project_id] = (revision, 0)
            self._wake_events.setdefault(project_id, asyncio.Event()).set()
            task = self._ensure_worker(project_id)
            await task

            pending = self._pending.get(project_id)
            if pending is None:
                return
            failure = self._errors.get(project_id)
            if failure is not None and failure.revision == pending.revision:
                raise AutosaveWriteError(failure)
            # A newer revision may have arrived at the boundary where the last
            # worker finished. Rejoin/create its worker rather than reporting an
            # older revision's failure as the result of this flush.

    async def close(self) -> None:
        """Flush all projects on shutdown, reporting every failed project."""
        if self._closed:
            return
        self._closed = True
        if self.policy.flush_on_shutdown:
            failures: list[AutosaveFailure] = []
            for project_id in tuple(self._pending):
                try:
                    await self.flush(project_id)
                except AutosaveWriteError as exc:
                    failures.append(exc.failure)
            if failures:
                raise AutosaveShutdownError(tuple(failures))
        else:
            tasks = tuple(self._workers.values())
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._workers.clear()
            self._pending.clear()
            self._retry_state.clear()
            self._force_flush.clear()
            self._wake_events.clear()
