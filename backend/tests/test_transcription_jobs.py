"""Focused API tests for backend-created Faster-Whisper transcription jobs."""

from dataclasses import replace
import threading
import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.projects.repository import ProjectRepository
from transcription import TranscriptionResult, TranscriptSegment, TranscriptWord


def valid_result() -> TranscriptionResult:
    word = TranscriptWord(" Hola", 0.0, 0.8, 0.98)
    segment = TranscriptSegment(0.0, 1.0, " Hola", (word,))
    return TranscriptionResult("es", 0.97, (segment,))


class TranscriptionJobApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name) / "projects"
        self.settings_patch = patch(
            "app.main.settings",
            replace(__import__("app.main", fromlist=["settings"]).settings,
                    project_data_dir=self.project_root),
        )
        self.settings_patch.start()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.settings_patch.stop()
        self.temp_dir.cleanup()

    def create_project_with_media(self, *, has_audio: bool = True) -> tuple[str, Path]:
        response = self.client.post("/api/v1/projects", json={"name": "Transcripción"})
        self.assertEqual(response.status_code, 201, response.text)
        project_id = response.json()["id"]
        repository: ProjectRepository = app.state.project_repository
        media_path = repository.media_path(project_id, "voz.mp4")
        media_path.write_bytes(b"mock mp4 data")
        record = repository.get(project_id)
        record["media"] = {
            "filename": "voz.mp4",
            "relative_path": f"{project_id}/media/voz.mp4",
            "duration": 1.0,
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "has_audio": has_audio,
            "probe_error": None,
        }
        repository.save(project_id, record)
        return project_id, media_path

    def wait_for_job(self, job_id: str) -> dict:
        for _ in range(100):
            response = self.client.get(f"/api/v1/transcription-jobs/{job_id}")
            self.assertEqual(response.status_code, 200, response.text)
            job = response.json()
            if job["state"] in {"succeeded", "failed", "cancelled"}:
                return job
            time.sleep(0.01)
        self.fail("Transcription job did not reach a terminal state")

    def test_project_not_found_is_rejected(self):
        response = self.client.post(
            f"/api/v1/projects/{uuid4()}/transcription-jobs", json={},
        )
        self.assertEqual(response.status_code, 404)

    def test_video_without_audio_is_rejected_before_job_creation(self):
        project_id, _ = self.create_project_with_media(has_audio=False)
        response = self.client.post(
            f"/api/v1/projects/{project_id}/transcription-jobs", json={},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("audio", response.json()["detail"].lower())

    def test_valid_result_is_saved_once_in_canonical_document_and_job_succeeds(self):
        project_id, media_path = self.create_project_with_media()
        self.client.patch(
            f"/api/v1/projects/{project_id}",
            json={"document": {"design": {"fontFamily": "Anton"}}},
        )
        fake_transcribe = AsyncMock(return_value=valid_result())
        with patch("app.api.v1.transcriptions.transcribe_media", fake_transcribe):
            response = self.client.post(
                f"/api/v1/projects/{project_id}/transcription-jobs",
                json={"model_size": "base", "language": "es"},
            )
            self.assertEqual(response.status_code, 202, response.text)
            job = self.wait_for_job(response.json()["job_id"])

        self.assertEqual(job["state"], "succeeded")
        fake_transcribe.assert_awaited_once()
        self.assertEqual(fake_transcribe.await_args.args[0], media_path.resolve())
        config = fake_transcribe.await_args.args[1]
        self.assertEqual((config.model_size, config.language, config.device, config.compute_type),
                         ("base", "es", "cpu", "int8"))
        saved = app.state.project_repository.get(project_id)
        self.assertEqual(saved["document"]["design"], {"fontFamily": "Anton"})
        self.assertEqual(saved["document"]["transcription"]["language"], "es")
        self.assertEqual(saved["document"]["transcription"]["segments"][0]["words"][0]["start"], 0.0)
        self.assertEqual(saved["transcription_info"]["word_count"], 1)
        self.assertIsNone(saved["transcription"])

    def test_invalid_word_timestamps_fail_without_altering_project(self):
        project_id, _ = self.create_project_with_media()
        invalid = TranscriptionResult(
            "es", 0.9,
            (TranscriptSegment(0.0, 1.0, " Hola", (TranscriptWord(" Hola", 0.4, 0.4),)),),
        )
        with patch("app.api.v1.transcriptions.transcribe_media", AsyncMock(return_value=invalid)):
            response = self.client.post(
                f"/api/v1/projects/{project_id}/transcription-jobs", json={},
            )
            job = self.wait_for_job(response.json()["job_id"])

        self.assertEqual(job["state"], "failed")
        self.assertIn("positive duration", job["error"]["message"])
        saved = app.state.project_repository.get(project_id)
        self.assertEqual(saved["document"], {})
        self.assertIsNone(saved["transcription_info"])

    def test_invalid_segment_timestamps_fail_without_altering_project(self):
        project_id, _ = self.create_project_with_media()
        invalid = TranscriptionResult(
            "es", 0.9, (TranscriptSegment(0.5, 0.5, "", ()),),
        )
        with patch("app.api.v1.transcriptions.transcribe_media", AsyncMock(return_value=invalid)):
            response = self.client.post(
                f"/api/v1/projects/{project_id}/transcription-jobs", json={},
            )
            job = self.wait_for_job(response.json()["job_id"])

        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["error"]["code"], "invalid_transcription_result")
        self.assertIn("positive duration", job["error"]["message"])
        saved = app.state.project_repository.get(project_id)
        self.assertEqual(saved["document"], {})
        self.assertIsNone(saved["transcription_info"])

    def test_concurrent_transcription_is_not_overwritten(self):
        project_id, media_path = self.create_project_with_media()
        worker_started = threading.Event()
        release_worker = threading.Event()

        def blocked_worker(_media_path, _config):
            worker_started.set()
            if not release_worker.wait(timeout=5):
                raise TimeoutError("Test worker release timed out")
            return valid_result()

        try:
            with patch("app.transcription_service._transcribe_sync", side_effect=blocked_worker):
                response = self.client.post(
                    f"/api/v1/projects/{project_id}/transcription-jobs", json={},
                )
                self.assertEqual(response.status_code, 202, response.text)
                job_id = response.json()["job_id"]
                self.assertTrue(worker_started.wait(timeout=2))

                concurrent = {
                    "language": "es",
                    "language_probability": 1.0,
                    "segments": [{
                        "id": "manual-segment", "start": 0.0, "end": 1.0, "text": "Editada",
                        "words": [{"id": "manual-word", "text": "Editada", "start": 0.0, "end": 1.0}],
                    }],
                }
                updated = self.client.patch(
                    f"/api/v1/projects/{project_id}",
                    json={"document": {"transcription": concurrent}},
                )
                self.assertEqual(updated.status_code, 200, updated.text)
                release_worker.set()
                job = self.wait_for_job(job_id)
        finally:
            release_worker.set()

        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["error"]["code"], "transcription_conflict")
        saved = app.state.project_repository.get(project_id)
        self.assertEqual(saved["document"]["transcription"], concurrent)
        self.assertIsNone(saved["transcription_info"])


if __name__ == "__main__":
    unittest.main()
