import asyncio
import copy
import unittest

from fastapi.testclient import TestClient

from app.jobs import InvalidJobTransition, JobManager, JobState
from app.main import app
from app.persistence import (
    AutosaveCoordinator,
    AutosavePolicy,
    AutosaveShutdownError,
    AutosaveWriteError,
)


class HealthEndpointTests(unittest.TestCase):
    def test_health_check(self):
        with TestClient(app) as client:
            response = client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["application"], "ARMA-TE Subtitle Editor API")

    def test_development_cors_preflight(self):
        with TestClient(app) as client:
            response = client.options(
                "/api/v1/health",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "GET",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5173")


class JobManagerTests(unittest.TestCase):
    def test_structured_errors_remain_retrievable(self):
        manager = JobManager()
        job = manager.create("future-export")
        manager.start(job.job_id)
        failed = manager.fail(
            job.job_id, "render_failed", "FFmpeg exited with code 1", retryable=True
        )
        restored = manager.get(job.job_id)
        self.assertEqual(failed.state, JobState.FAILED)
        self.assertEqual(restored.error.code, "render_failed")
        self.assertTrue(restored.error.retryable)
        # The original flat accessors remain available for callers during migration.
        self.assertEqual(restored.error_code, "render_failed")
        self.assertEqual(restored.error_message, "FFmpeg exited with code 1")

    def test_queued_job_can_be_cancelled_before_execution(self):
        manager = JobManager()
        job = manager.create("future-transcription")
        cancelled = manager.request_cancel(job.job_id)
        self.assertEqual(cancelled.state, JobState.CANCELLED)
        self.assertTrue(cancelled.cancel_requested)
        self.assertEqual(manager.request_cancel(job.job_id).state, JobState.CANCELLED)
        with self.assertRaises(InvalidJobTransition):
            manager.start(job.job_id)

    def test_running_cancel_is_only_a_request_until_worker_acknowledges(self):
        manager = JobManager()
        job = manager.create("future-render")
        manager.start(job.job_id)
        with self.assertRaises(InvalidJobTransition):
            manager.acknowledge_cancel(job.job_id)
        requested = manager.request_cancel(job.job_id)
        self.assertEqual(requested.state, JobState.RUNNING)
        self.assertTrue(manager.is_cancel_requested(job.job_id))
        cancelled = manager.acknowledge_cancel(job.job_id)
        self.assertEqual(cancelled.state, JobState.CANCELLED)

    def test_completed_job_cannot_be_relabelled_cancelled(self):
        manager = JobManager()
        job = manager.create("future-render")
        manager.start(job.job_id)
        manager.succeed(job.job_id)
        with self.assertRaises(InvalidJobTransition):
            manager.request_cancel(job.job_id)
        self.assertEqual(manager.get(job.job_id).state, JobState.SUCCEEDED)

    def test_progress_is_finite_monotonic_and_running_only(self):
        manager = JobManager()
        job = manager.create("future-task")
        with self.assertRaises(InvalidJobTransition):
            manager.update_progress(job.job_id, 10)
        manager.start(job.job_id)
        manager.update_progress(job.job_id, 30)
        with self.assertRaises(ValueError):
            manager.update_progress(job.job_id, 20)
        with self.assertRaises(ValueError):
            manager.update_progress(job.job_id, float("nan"))


class AutosaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_debounce_coalesces_to_latest_snapshot(self):
        writes = []

        async def writer(project_id, snapshot):
            writes.append((project_id, dict(snapshot)))

        autosave = AutosaveCoordinator(writer, AutosavePolicy(debounce_seconds=0.02))
        autosave.schedule("project-1", {"text": "first"})
        autosave.schedule("project-1", {"text": "latest"})
        # Allow scheduling jitter on loaded Windows CI while still proving that
        # debounce completes without an explicit flush.
        for _ in range(100):
            if writes:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(writes, [("project-1", {"text": "latest"})])
        self.assertFalse(autosave.has_pending("project-1"))
        await autosave.close()

    async def test_flush_writes_pending_snapshot_without_waiting_for_debounce(self):
        writes = []

        async def writer(project_id, snapshot):
            writes.append((project_id, dict(snapshot)))

        autosave = AutosaveCoordinator(writer, AutosavePolicy(debounce_seconds=5))
        autosave.schedule("project-2", {"revision": 4})
        await autosave.flush("project-2")
        self.assertEqual(writes, [("project-2", {"revision": 4})])
        await autosave.close()

    async def test_snapshot_is_deeply_isolated_and_retry_uses_pristine_data(self):
        attempts = []

        async def writer(project_id, snapshot):
            attempts.append(copy.deepcopy(snapshot))
            if len(attempts) == 1:
                snapshot["document"]["tokens"].append("writer mutation")
                raise OSError("temporary write failure")

        source = {"document": {"tokens": ["initial"]}}
        autosave = AutosaveCoordinator(
            writer,
            AutosavePolicy(debounce_seconds=5, retry_attempts=1, retry_backoff_seconds=0),
        )
        autosave.schedule("project-3", source)
        source["document"]["tokens"].append("caller mutation")
        with self.assertLogs("app.persistence.autosave", level="ERROR"):
            await autosave.flush("project-3")

        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], {"document": {"tokens": ["initial"]}})
        self.assertEqual(attempts[1], {"document": {"tokens": ["initial"]}})
        self.assertIsNone(autosave.get_error("project-3"))
        self.assertFalse(autosave.has_pending("project-3"))
        await autosave.close()

    async def test_changes_scheduled_during_failed_write_are_not_lost(self):
        first_write_started = asyncio.Event()
        release_first_write = asyncio.Event()
        writes = []

        async def writer(project_id, snapshot):
            writes.append(snapshot["revision"])
            if snapshot["revision"] == 1:
                first_write_started.set()
                await release_first_write.wait()
                raise OSError("old revision failed")

        autosave = AutosaveCoordinator(
            writer,
            AutosavePolicy(debounce_seconds=0, retry_attempts=0),
        )
        autosave.schedule("project-4", {"revision": 1})
        await asyncio.wait_for(first_write_started.wait(), timeout=1)
        autosave.schedule("project-4", {"revision": 2})
        release_first_write.set()

        with self.assertLogs("app.persistence.autosave", level="ERROR"):
            await autosave.flush("project-4")

        self.assertEqual(writes, [1, 2])
        self.assertFalse(autosave.has_pending("project-4"))
        self.assertIsNone(autosave.get_error("project-4"))
        await autosave.close()

    async def test_permanent_writer_failure_is_logged_queryable_and_raised(self):
        calls = 0

        async def writer(project_id, snapshot):
            nonlocal calls
            calls += 1
            raise OSError("disk unavailable")

        autosave = AutosaveCoordinator(
            writer,
            AutosavePolicy(debounce_seconds=5, retry_attempts=2, retry_backoff_seconds=0),
        )
        autosave.schedule("project-5", {"text": "retained"})
        with self.assertLogs("app.persistence.autosave", level="ERROR"):
            with self.assertRaises(AutosaveWriteError):
                await autosave.flush("project-5")

        failure = autosave.get_error("project-5")
        self.assertEqual(calls, 3)  # initial write plus two bounded retries
        self.assertEqual(failure.attempts, 3)
        self.assertEqual(failure.message, "disk unavailable")
        self.assertTrue(autosave.has_pending("project-5"))

    async def test_new_revision_recovers_after_previous_revision_exhausts_retries(self):
        writes = []

        async def writer(project_id, snapshot):
            writes.append(snapshot["revision"])
            if snapshot["revision"] == 1:
                raise OSError("old snapshot failed")

        autosave = AutosaveCoordinator(
            writer,
            AutosavePolicy(debounce_seconds=0, retry_attempts=0),
        )
        autosave.schedule("project-7", {"revision": 1})
        with self.assertLogs("app.persistence.autosave", level="ERROR"):
            with self.assertRaises(AutosaveWriteError):
                await autosave.flush("project-7")

        autosave.schedule("project-7", {"revision": 2})
        await autosave.flush("project-7")
        self.assertEqual(writes, [1, 2])
        self.assertFalse(autosave.has_pending("project-7"))
        self.assertIsNone(autosave.get_error("project-7"))
        await autosave.close()

    async def test_close_flushes_pending_snapshot(self):
        writes = []

        async def writer(project_id, snapshot):
            writes.append((project_id, dict(snapshot)))

        autosave = AutosaveCoordinator(writer, AutosavePolicy(debounce_seconds=5))
        autosave.schedule("project-6", {"revision": 4})
        await autosave.close()
        self.assertEqual(writes, [("project-6", {"revision": 4})])

    async def test_close_reports_all_permanently_unsaved_projects(self):
        async def writer(project_id, snapshot):
            raise OSError(f"cannot write {project_id}")

        autosave = AutosaveCoordinator(
            writer,
            AutosavePolicy(debounce_seconds=5, retry_attempts=0),
        )
        autosave.schedule("project-a", {"value": 1})
        autosave.schedule("project-b", {"value": 2})
        with self.assertLogs("app.persistence.autosave", level="ERROR"):
            with self.assertRaises(AutosaveShutdownError) as caught:
                await autosave.close()
        self.assertEqual({failure.project_id for failure in caught.exception.failures},
                         {"project-a", "project-b"})


if __name__ == "__main__":
    unittest.main()
