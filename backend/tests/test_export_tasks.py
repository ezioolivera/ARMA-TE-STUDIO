import asyncio
from dataclasses import replace
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.exports.fonts import FontPreparationError
from app.main import app
from app.projects.repository import ProjectRepository


class _AsyncPipe:
    def __init__(self, values):
        self.values = iter(values)

    async def readline(self):
        return next(self.values, b"")

    async def read(self):
        return b""


class _FakeProcess:
    def __init__(self):
        self.stdout = _AsyncPipe([b"out_time_us=1000000\n", b""])
        self.stderr = _AsyncPipe([])
        self.returncode = None
        self.terminated = False

    async def wait(self):
        self.returncode = 0
        return 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True
        self.returncode = -9


class _SlowFakeProcess(_FakeProcess):
    def __init__(self):
        super().__init__()
        self.stdout = self

    async def readline(self):
        await asyncio.sleep(.05)
        return b"out_time_us=0\n"


class _FailedFakeProcess(_FakeProcess):
    async def wait(self):
        self.returncode = 1
        return 1


class _ErrorPipe(_AsyncPipe):
    async def read(self):
        return b"fixture render failure"


class ExportTaskApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name) / "projects"
        settings = replace(__import__("app.main", fromlist=["settings"]).settings, project_data_dir=self.project_root)
        self.settings_patch = patch("app.main.settings", settings)
        self.settings_patch.start()
        self.context = TestClient(app, client=("127.0.0.1", 50001))
        self.client = self.context.__enter__()
        self.project = self.client.post("/api/v1/projects", json={"name": "Task trace"}).json()
        self.media_path = self.project_root / self.project["id"] / "media" / "clip.mp4"
        self.media_path.parent.mkdir(parents=True, exist_ok=True)
        self.media_path.write_bytes(b"fake media")
        repository = ProjectRepository(self.project_root)
        record = repository.get(self.project["id"])
        record["media"] = {
            "filename": "clip.mp4", "duration": 1.0, "width": 320,
            "height": 240, "fps": 24, "has_audio": True,
        }
        repository.save(self.project["id"], record)

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.settings_patch.stop()
        self.temp_dir.cleanup()

    def _start_export(self):
        response = self.client.post(
            f"/api/v1/projects/{self.project['id']}/exports",
            json={"format": "mp4", "quality": "compact"},
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["job_id"]

    def _wait_terminal(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            record = self.client.get(f"/api/v1/jobs/{job_id}").json()
            if record["state"] in {"succeeded", "failed", "cancelled"}:
                return record
            time.sleep(.01)
        self.fail(f"Job {job_id} did not reach a terminal state")

    def _enable_fake_ffmpeg(self):
        app_settings = replace(
            __import__("app.main", fromlist=["settings"]).settings,
            project_data_dir=self.project_root, ffmpeg_path="ffmpeg",
        )
        return patch("app.api.v1.exports.settings", app_settings), patch(
            "app.api.v1.exports.shutil.which", return_value="ffmpeg",
        )

    def test_export_success_records_every_major_stage_and_task_result(self):
        from app.api.v1 import exports

        fake_process = _FakeProcess()

        async def fake_exec(*args, **_kwargs):
            Path(args[-1]).write_bytes(b"rendered mp4 bytes")
            return fake_process

        def fake_ass(_record, _layout, **kwargs):
            with kwargs["stage_context"]("geometric_validation"):
                pass
            with kwargs["stage_context"]("ass_generation"):
                return "[Script Info]\n[Events]\n"

        settings_patch, which_patch = self._enable_fake_ffmpeg()
        with settings_patch, which_patch, \
             patch.object(exports, "materialize_bundled_font", return_value=Path("local.ttf")), \
             patch.object(exports, "export_ass_text", side_effect=fake_ass), \
             patch.object(exports, "_verify_ass_font", new=AsyncMock(return_value="Anton")), \
             patch("app.api.v1.exports.AsyncSubprocess.create", new=AsyncMock(side_effect=fake_exec)):
            job_id = self._start_export()
            result = self._wait_terminal(job_id)

        self.assertEqual(result["state"], "succeeded", result)
        diagnostics = self.client.get(f"/api/v1/jobs/{job_id}/diagnostics").json()
        self.assertEqual(diagnostics["task_status"], "completed")
        stages = {event["stage"] for event in diagnostics["events"]}
        self.assertTrue({
            "preparation_start", "route_validation", "input_output_preparation",
            "font_materialization", "geometric_validation", "ass_generation",
            "ass_write", "font_probe_ffmpeg", "ffmpeg_launch", "ffmpeg_render",
            "job_finalization", "task_finished",
        }.issubset(stages), stages)
        self.assertTrue(all("timestamp" in event and "job_id" in event for event in diagnostics["events"]))
        self.assertTrue(all("duration_ms" in event for event in diagnostics["events"] if event["result"] == "completed"))

    def test_preparation_error_is_visible_and_job_fails(self):
        repository = ProjectRepository(self.project_root)
        record = repository.get(self.project["id"])
        record["media"]["filename"] = "missing.mp4"
        repository.save(self.project["id"], record)

        job_id = self._start_export()
        result = self._wait_terminal(job_id)
        self.assertEqual(result["state"], "failed")
        diagnostics = self.client.get(f"/api/v1/jobs/{job_id}/diagnostics").json()
        self.assertEqual(diagnostics["task_status"], "completed")
        self.assertTrue(any(
            event["stage"] == "route_validation" and event["result"] == "error"
            for event in diagnostics["events"]
        ))

    def test_font_materialization_and_font_probe_errors_are_recorded(self):
        from app.api.v1 import exports

        settings_patch, which_patch = self._enable_fake_ffmpeg()
        with settings_patch, which_patch, patch.object(
            exports, "materialize_bundled_font", side_effect=FontPreparationError("fixture font failure"),
        ):
            job_id = self._start_export()
            result = self._wait_terminal(job_id)
        self.assertEqual(result["state"], "failed")
        diagnostics = self.client.get(f"/api/v1/jobs/{job_id}/diagnostics").json()
        self.assertTrue(any(
            event["stage"] == "font_materialization" and event["result"] == "error"
            and event.get("error_type") == "FontPreparationError"
            for event in diagnostics["events"]
        ))

        with settings_patch, which_patch, \
             patch.object(exports, "materialize_bundled_font", return_value=Path("local.ttf")), \
             patch.object(exports, "export_ass_text", return_value="[Script Info]\n[Events]\n"), \
             patch.object(exports, "_verify_ass_font", new=AsyncMock(side_effect=FontPreparationError("fixture probe failure"))):
            probe_job = self._start_export()
            probe_result = self._wait_terminal(probe_job)
        self.assertEqual(probe_result["state"], "failed")
        diagnostics = self.client.get(f"/api/v1/jobs/{probe_job}/diagnostics").json()
        self.assertTrue(any(
            event["stage"] == "font_probe_ffmpeg" and event["result"] == "error"
            for event in diagnostics["events"]
        ))

    def test_ffmpeg_launch_error_is_logged_and_reconciled(self):
        from app.api.v1 import exports

        settings_patch, which_patch = self._enable_fake_ffmpeg()
        with settings_patch, which_patch, \
             patch.object(exports, "materialize_bundled_font", return_value=Path("local.ttf")), \
             patch.object(exports, "export_ass_text", return_value="[Script Info]\n[Events]\n"), \
             patch.object(exports, "_verify_ass_font", new=AsyncMock(return_value="Anton")), \
             patch("app.api.v1.exports.AsyncSubprocess.create", new=AsyncMock(side_effect=OSError("fixture ffmpeg launch failure"))):
            job_id = self._start_export()
            result = self._wait_terminal(job_id)

        self.assertEqual(result["state"], "failed")
        diagnostics = self.client.get(f"/api/v1/jobs/{job_id}/diagnostics").json()
        self.assertTrue(any(
            event["stage"] == "ffmpeg_launch" and event["result"] == "error"
            and event.get("error_type") == "OSError"
            for event in diagnostics["events"]
        ))

    def test_empty_not_implemented_error_keeps_type_message_and_traceback(self):
        from app.api.v1 import exports

        settings_patch, which_patch = self._enable_fake_ffmpeg()
        with settings_patch, which_patch, \
             patch.object(exports, "materialize_bundled_font", return_value=Path("local.ttf")), \
             patch.object(exports, "export_ass_text", return_value="[Script Info]\n[Events]\n"), \
             patch.object(exports, "_verify_ass_font", new=AsyncMock(side_effect=NotImplementedError())):
            job_id = self._start_export()
            result = self._wait_terminal(job_id)

        self.assertEqual(result["state"], "failed")
        self.assertIn("NotImplementedError", result["error"]["message"])
        diagnostics = self.client.get(f"/api/v1/jobs/{job_id}/diagnostics").json()
        self.assertEqual(diagnostics["exception"]["type"], "NotImplementedError")
        self.assertIn("Traceback (most recent call last)", diagnostics["exception"]["traceback"])
        self.assertIn("_render_mp4", diagnostics["exception"]["traceback"])
        self.assertNotIn("local.ttf", diagnostics["exception"]["traceback"])
        self.assertTrue(any(
            event["stage"] == "font_probe_ffmpeg" and event.get("traceback")
            for event in diagnostics["events"]
        ))

    @unittest.skipUnless(sys.platform == "win32", "Uvicorn uses a selector subprocess loop only on Windows")
    def test_async_process_adapter_runs_on_uvicorn_reload_selector_loop(self):
        from app.exports.async_process import AsyncSubprocess
        from uvicorn import Config

        config = Config("app.main:app", loop="auto", reload=True)
        loop_factory = config.get_loop_factory()
        loop = loop_factory()
        self.assertIn("SelectorEventLoop", type(loop).__name__)

        async def smoke():
            process = await AsyncSubprocess.create(
                sys.executable, "-c",
                "import sys; print('stdout-ok'); print('stderr-ok', file=sys.stderr)",
            )
            stdout, stderr = await process.communicate()
            self.assertEqual(process.returncode, 0)
            self.assertIn(b"stdout-ok", stdout)
            self.assertIn(b"stderr-ok", stderr)

        try:
            loop.run_until_complete(smoke())
        finally:
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("ffmpeg"), "Windows and FFmpeg are required for font-probe smoke test")
    def test_ffmpeg_font_probe_succeeds_on_uvicorn_reload_selector_loop(self):
        from app.api.v1.exports import _verify_ass_font
        from app.exports.fonts import materialize_bundled_font
        from uvicorn import Config

        with tempfile.TemporaryDirectory() as temp:
            font_dir = Path(temp)
            font_path = materialize_bundled_font("Anton", font_dir)
            self.assertIsNotNone(font_path)
            ass_path = font_dir / "probe.ass"
            ass_path.write_text(
                "[Script Info]\nScriptType: v4.00+\nPlayResX: 64\nPlayResY: 64\n"
                "[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
                "Style: Base,Anton,12,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,1,1,0,2,10,10,10,1\n"
                "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n",
                encoding="utf-8",
            )
            config = Config("app.main:app", loop="auto", reload=True)
            loop = config.get_loop_factory()()
            try:
                selected = loop.run_until_complete(_verify_ass_font(
                    shutil.which("ffmpeg"), ass_path, font_dir, "Anton",
                ))
            finally:
                loop.run_until_complete(loop.shutdown_default_executor())
                loop.close()
        self.assertIn("Anton", selected)

    @unittest.skipUnless(sys.platform == "win32" and shutil.which("ffmpeg"), "Windows and FFmpeg are required for render smoke test")
    def test_ffmpeg_render_with_progress_succeeds_on_uvicorn_reload_selector_loop(self):
        from app.exports.async_process import AsyncSubprocess
        from uvicorn import Config

        with tempfile.TemporaryDirectory() as temp:
            output_path = Path(temp) / "selector-render.mp4"
            config = Config("app.main:app", loop="auto", reload=True)
            loop = config.get_loop_factory()()

            async def render():
                process = await AsyncSubprocess.create(
                    shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostats",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-progress", "pipe:1", "-y", str(output_path),
                )
                stderr_task = asyncio.create_task(process.stderr.read())
                progress_lines = []
                while line := await process.stdout.readline():
                    progress_lines.append(line.decode("ascii", errors="replace").strip())
                return_code = await process.wait()
                stderr = await stderr_task
                return return_code, progress_lines, stderr

            try:
                return_code, progress_lines, stderr = loop.run_until_complete(render())
            finally:
                loop.run_until_complete(loop.shutdown_default_executor())
                loop.close()
            self.assertEqual(return_code, 0, stderr.decode("utf-8", errors="replace"))
            self.assertTrue(output_path.is_file())
            self.assertGreater(output_path.stat().st_size, 500)
            self.assertTrue(any(line.startswith("out_time_us=") for line in progress_lines), progress_lines)

    def test_ffmpeg_nonzero_exit_is_recorded_as_render_failure(self):
        from app.api.v1 import exports

        fake_process = _FailedFakeProcess()
        fake_process.stderr = _ErrorPipe([])

        async def fake_exec(*args, **_kwargs):
            Path(args[-1]).write_bytes(b"partial")
            return fake_process

        settings_patch, which_patch = self._enable_fake_ffmpeg()
        with settings_patch, which_patch, \
             patch.object(exports, "materialize_bundled_font", return_value=Path("local.ttf")), \
             patch.object(exports, "export_ass_text", return_value="[Script Info]\n[Events]\n"), \
             patch.object(exports, "_verify_ass_font", new=AsyncMock(return_value="Anton")), \
             patch("app.api.v1.exports.AsyncSubprocess.create", new=AsyncMock(side_effect=fake_exec)):
            job_id = self._start_export()
            result = self._wait_terminal(job_id)

        self.assertEqual(result["state"], "failed")
        self.assertIn("fixture render failure", result["error"]["message"])
        diagnostics = self.client.get(f"/api/v1/jobs/{job_id}/diagnostics").json()
        self.assertTrue(any(
            event["stage"] == "ffmpeg_render" and event["result"] == "error"
            and event["error_type"] == "RuntimeError"
            for event in diagnostics["events"]
        ))

    def test_cooperative_cancel_stops_process_and_preserves_cancelled_job_state(self):
        from app.api.v1 import exports

        fake_process = _SlowFakeProcess()

        async def fake_exec(*args, **_kwargs):
            Path(args[-1]).write_bytes(b"partial mp4")
            return fake_process

        settings_patch, which_patch = self._enable_fake_ffmpeg()
        with settings_patch, which_patch, \
             patch.object(exports, "materialize_bundled_font", return_value=Path("local.ttf")), \
             patch.object(exports, "export_ass_text", return_value="[Script Info]\n[Events]\n"), \
             patch.object(exports, "_verify_ass_font", new=AsyncMock(return_value="Anton")), \
             patch("app.api.v1.exports.AsyncSubprocess.create", new=AsyncMock(side_effect=fake_exec)):
            job_id = self._start_export()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                details = self.client.get(f"/api/v1/jobs/{job_id}/diagnostics").json()
                if details.get("current_stage") == "ffmpeg_render":
                    break
                time.sleep(.01)
            else:
                self.fail("job did not reach FFmpeg render stage")
            cancelled = self.client.post(f"/api/v1/jobs/{job_id}/cancel")
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            result = self._wait_terminal(job_id)

        self.assertEqual(result["state"], "cancelled")
        self.assertTrue(fake_process.terminated)
        diagnostics = self.client.get(f"/api/v1/jobs/{job_id}/diagnostics").json()
        self.assertEqual(diagnostics["task_status"], "completed")
        self.assertTrue(any(event["stage"] == "task_finished" for event in diagnostics["events"]))
        self.assertTrue(any(
            event["stage"] == "ffmpeg_render" and event["result"] == "cancelled"
            for event in diagnostics["events"]
        ))

    def test_diagnostics_can_include_stack_only_for_loopback_clients(self):
        job = self.client.app.state.job_manager.create("video_export")
        response = self.client.get(f"/api/v1/jobs/{job.job_id}/diagnostics?include_stack=true")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["task_status"], "not_registered")
        self.assertEqual(response.json()["events"], [])


class TaskRegistryReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def test_finished_task_with_running_job_is_reconciled_to_failed(self):
        from app.api.v1.exports import _export_task_done
        from app.jobs import JobManager, TaskRegistry
        from types import SimpleNamespace

        manager = JobManager()
        job = manager.create("video_export")
        manager.start(job.job_id)
        registry = TaskRegistry()

        async def returns_early():
            return None

        task = asyncio.create_task(returns_early())
        registry.register(job.job_id, task)
        await task
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            job_manager=manager, export_task_registry=registry,
        )))
        _export_task_done(job.job_id, task, request)

        self.assertEqual(manager.get(job.job_id).state.value, "failed")
        self.assertEqual(registry.get(job.job_id)["task_status"], "completed")
        self.assertTrue(any(event["stage"] == "job_reconciliation" for event in registry.get(job.job_id)["events"]))

    async def test_uncaught_task_exception_is_retrieved_and_fails_job(self):
        from app.api.v1.exports import _export_task_done
        from app.jobs import JobManager, TaskRegistry
        from types import SimpleNamespace

        manager = JobManager()
        job = manager.create("video_export")
        manager.start(job.job_id)
        registry = TaskRegistry()

        async def raises():
            raise RuntimeError("uncaught fixture")

        task = asyncio.create_task(raises())
        registry.register(job.job_id, task)
        await asyncio.sleep(0)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            job_manager=manager, export_task_registry=registry,
        )))
        _export_task_done(job.job_id, task, request)

        self.assertEqual(manager.get(job.job_id).state.value, "failed")
        self.assertEqual(manager.get(job.job_id).error_code, "export_task_exception")
        self.assertEqual(registry.get(job.job_id)["task_status"], "exception")

    async def test_task_stack_is_available_while_task_is_active_and_released_afterward(self):
        from app.jobs import TaskRegistry

        registry = TaskRegistry()
        gate = asyncio.Event()

        async def waits():
            registry.mark_running("stack-job")
            await gate.wait()

        task = asyncio.create_task(waits())
        registry.register("stack-job", task)
        await asyncio.sleep(0)
        details = registry.get("stack-job", include_stack=True)
        self.assertEqual(details["task_status"], "running")
        self.assertTrue(details["stack"])
        gate.set()
        await task
        registry.finish("stack-job", task)
        self.assertEqual(registry.get("stack-job", include_stack=True)["stack"], [])


if __name__ == "__main__":
    unittest.main()
