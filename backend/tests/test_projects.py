"""Project persistence, imports, FFprobe metadata and playback API coverage."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.projects import audio as audio_module
from app.projects import media as media_module
from app.projects.models import AudioInfo, MediaInfo
from app.projects.repository import ProjectNotFound, ProjectRepository


class ProjectRepositoryTests(unittest.TestCase):
    def test_audio_probe_extracts_ffprobe_metadata_and_rejects_extension_mismatch(self):
        probe = {
            "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le", "sample_rate": "48000", "channels": 2, "duration": "2.5"}],
            "format": {"format_name": "wav", "duration": "2.5"},
        }
        with patch("app.projects.audio._probe_sync", return_value=probe):
            metadata = audio_module._probe_audio_sync(Path("audio.uploading"), ".wav")
            with self.assertRaises(media_module.InvalidMedia):
                audio_module._probe_audio_sync(Path("audio.uploading"), ".mp3")
        self.assertEqual(metadata, {
            "duration": 2.5, "sample_rate": 48000, "channels": 2,
            "codec": "pcm_s16le", "format_name": "wav",
        })

    def test_projects_have_stable_ids_and_recover_after_repository_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "projects"
            first = ProjectRepository(root)
            created = first.create("Viaje")
            project_id = created["id"]
            created["document"] = {"cursor": 42, "nested": ["guardado"]}
            first.save(project_id, created)

            restarted = ProjectRepository(root)
            restored = restarted.get(project_id)
            self.assertEqual(restored["id"], project_id)
            self.assertEqual(restored["document"]["nested"], ["guardado"])
            self.assertEqual([item["id"] for item in restarted.list()], [project_id])

    def test_repository_rejects_non_uuid_and_path_traversal_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ProjectRepository(Path(temp_dir))
            with self.assertRaises(ProjectNotFound):
                repository.get("..\\outside")

    def test_delete_removes_project_media_but_never_sibling_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "projects"
            repository = ProjectRepository(root)
            project = repository.create("Eliminar")
            project_dir = root / project["id"]
            (project_dir / "media" / "video.mp4").write_bytes(b"sample")
            outside = Path(temp_dir) / "outside.txt"
            outside.write_text("keep", encoding="utf-8")

            repository.delete(project["id"])

            self.assertFalse(project_dir.exists())
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")
            with self.assertRaises(ProjectNotFound):
                repository.get(project["id"])
            with self.assertRaises(ProjectNotFound):
                repository.delete("..\\outside")


class FFprobeTests(unittest.IsolatedAsyncioTestCase):
    async def test_ffprobe_stream_metadata_maps_duration_resolution_codecs_and_audio(self):
        probe_output = json.dumps({
            "format": {"duration": "12.5"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1080,
                 "height": 1920, "avg_frame_rate": "30000/1001"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        })
        completed = __import__("subprocess").CompletedProcess([], 0, probe_output, "")
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "sample.mp4"
            video.write_bytes(b"mock")
            with patch("app.projects.media.shutil.which", return_value="ffprobe.exe"), patch(
                "app.projects.media.subprocess.run", return_value=completed
            ) as run:
                info = await media_module.probe_video(video, "sample.mp4", "id/media/sample.mp4")
        self.assertEqual(info.duration, 12.5)
        self.assertEqual((info.width, info.height), (1080, 1920))
        self.assertAlmostEqual(info.fps, 29.97002997)
        self.assertEqual(info.video_codec, "h264")
        self.assertEqual(info.audio_codec, "aac")
        self.assertTrue(info.has_audio)
        self.assertIn("-show_streams", run.call_args.args[0])

    async def test_ffprobe_rejects_video_without_video_stream(self):
        completed = __import__("subprocess").CompletedProcess([], 0, '{"streams": []}', "")
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "audio-only.mp4"
            video.write_bytes(b"mock")
            with patch("app.projects.media.shutil.which", return_value="ffprobe.exe"), patch(
                "app.projects.media.subprocess.run", return_value=completed
            ):
                with self.assertRaises(media_module.InvalidMedia):
                    await media_module.probe_video(video, video.name, "x")


class ProjectApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name) / "project-store"
        self.settings_patch = patch(
            "app.main.settings",
            replace(__import__("app.main", fromlist=["settings"]).settings,
                    project_data_dir=self.project_root),
        )
        self.settings_patch.start()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.settings_patch.stop()
        self.temp_dir.cleanup()

    def create_project(self, name="Prueba"):
        response = self.client.post("/api/v1/projects", json={"name": name})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_create_list_open_update_and_recover_project(self):
        project = self.create_project()
        project_id = project["id"]
        update = self.client.patch(
            f"/api/v1/projects/{project_id}",
            json={"metadata": {"profile": "ARMA-TE EXPLORA"}, "document": {"revision": 3}},
        )
        self.assertEqual(update.status_code, 200, update.text)
        self.assertEqual(update.json()["document"], {"revision": 3})
        self.assertEqual(self.client.get("/api/v1/projects").json()[0]["id"], project_id)

        self.client_context.__exit__(None, None, None)
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        restored = self.client.get(f"/api/v1/projects/{project_id}")
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["metadata"]["profile"], "ARMA-TE EXPLORA")
        self.assertEqual(restored.json()["document"]["revision"], 3)

    def test_delete_project_removes_document_and_associated_media(self):
        project = self.create_project()
        project_id = project["id"]
        media_file = self.project_root / project_id / "media" / "clip.mp4"
        media_file.write_bytes(b"test video")

        response = self.client.delete(f"/api/v1/projects/{project_id}")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"id": project_id, "deleted": True})
        self.assertFalse((self.project_root / project_id).exists())
        self.assertNotIn(project_id, [item["id"] for item in self.client.get("/api/v1/projects").json()])
        self.assertEqual(self.client.get(f"/api/v1/projects/{project_id}").status_code, 404)

    def test_delete_invalid_or_missing_project_does_not_touch_sibling_directories(self):
        outside = self.project_root.parent / "outside.txt"
        outside.write_text("keep", encoding="utf-8")
        invalid = self.client.delete("/api/v1/projects/..%5Coutside")
        missing = self.client.delete("/api/v1/projects/00000000-0000-0000-0000-000000000001")
        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_import_media_copies_file_and_streams_full_and_range_requests(self):
        project = self.create_project()
        project_id = project["id"]
        video_bytes = b"0123456789-video-content"
        with patch(
            "app.api.v1.projects.probe_video",
            new=AsyncMock(return_value=MediaInfo(
                filename="sample.mp4", relative_path=f"{project_id}/media/sample.mp4",
                duration=2.5, width=1920, height=1080, fps=30,
                video_codec="h264", audio_codec="aac", has_audio=True,
            )),
        ):
            imported = self.client.post(
                f"/api/v1/projects/{project_id}/media?filename=sample.mp4",
                content=video_bytes,
                headers={"Content-Type": "video/mp4"},
            )
        self.assertEqual(imported.status_code, 201, imported.text)
        self.assertEqual(imported.json()["width"], 1920)
        stored = self.project_root / project_id / "media" / "sample.mp4"
        self.assertEqual(stored.read_bytes(), video_bytes)

        full = self.client.get(f"/api/v1/projects/{project_id}/media")
        self.assertEqual(full.status_code, 200)
        self.assertEqual(full.content, video_bytes)
        ranged = self.client.get(
            f"/api/v1/projects/{project_id}/media", headers={"Range": "bytes=2-7"}
        )
        self.assertEqual(ranged.status_code, 206)
        self.assertEqual(ranged.content, video_bytes[2:8])
        self.assertEqual(ranged.headers["content-range"], f"bytes 2-7/{len(video_bytes)}")
        unsatisfiable = self.client.get(
            f"/api/v1/projects/{project_id}/media", headers={"Range": "bytes=999-"}
        )
        self.assertEqual(unsatisfiable.status_code, 416)

    def test_import_audio_validates_and_persists_ffprobe_metadata_without_replacing_video(self):
        project = self.create_project()
        project_id = project["id"]
        video_before = {
            "filename": "clip.mp4", "relative_path": f"{project_id}/media/clip.mp4",
            "duration": 1.0, "width": 640, "height": 360, "fps": 30,
            "video_codec": "h264", "audio_codec": "aac", "has_audio": True, "probe_error": None,
        }
        record = app.state.project_repository.get(project_id)
        record["media"] = video_before
        app.state.project_repository.save(project_id, record)
        audio_info = AudioInfo(
            filename="voice.wav", relative_path=f"{project_id}/media/voice.wav", duration=4.25,
            sample_rate=48000, channels=2, codec="pcm_s16le", format_name="wav",
        )
        with patch("app.api.v1.projects.probe_audio", new=AsyncMock(return_value=audio_info)):
            imported = self.client.post(
                f"/api/v1/projects/{project_id}/audio?filename=voice.wav", content=b"wav bytes",
            )
        self.assertEqual(imported.status_code, 201, imported.text)
        stored = self.project_root / project_id / "media" / "voice.wav"
        self.assertEqual(stored.read_bytes(), b"wav bytes")
        reopened = self.client.get(f"/api/v1/projects/{project_id}").json()
        self.assertEqual(reopened["audio"]["sample_rate"], 48000)
        self.assertEqual(reopened["audio"]["duration"], 4.25)
        self.assertEqual(reopened["media"], video_before)
        transcript = {"language": "es", "segments": [{"text": "Anterior"}]}
        record = app.state.project_repository.get(project_id)
        record["document"] = {
            "design": {"fontFamily": "Anton"}, "transcription": transcript,
            "phrase_transcription": {"version": 1, "phrases": [{"id": "F001"}]},
        }
        record["transcription"] = transcript
        record["transcription_info"] = {"filename": "voice.json", "language": "es", "segment_count": 1, "word_count": 1}
        app.state.project_repository.save(project_id, record)
        mp3_info = AudioInfo(
            filename="voice-v2.mp3", relative_path=f"{project_id}/media/voice-v2.mp3", duration=5.0,
            sample_rate=44100, channels=2, codec="mp3", format_name="mp3",
        )
        with patch("app.api.v1.projects.probe_audio", new=AsyncMock(return_value=mp3_info)):
            replaced = self.client.post(
                f"/api/v1/projects/{project_id}/audio?filename=voice-v2.mp3", content=b"mp3 bytes",
            )
        self.assertEqual(replaced.status_code, 201, replaced.text)
        reopened = self.client.get(f"/api/v1/projects/{project_id}").json()
        self.assertEqual(reopened["audio"]["codec"], "mp3")
        self.assertIsNone(reopened["transcription_info"])
        self.assertNotIn("transcription", reopened["document"])
        self.assertNotIn("phrase_transcription", reopened["document"])
        self.assertEqual(reopened["document"]["design"], {"fontFamily": "Anton"})
        self.assertFalse(stored.exists())
        self.assertTrue((self.project_root / project_id / "media" / "voice-v2.mp3").exists())
        self.assertEqual(reopened["media"], video_before)

    def test_replace_mp3_with_wav_and_invalid_replacement_preserves_current_audio_and_transcript(self):
        project = self.create_project()
        project_id = project["id"]
        old_path = self.project_root / project_id / "media" / "voice.mp3"
        old_path.write_bytes(b"old mp3")
        record = app.state.project_repository.get(project_id)
        old_audio = AudioInfo(
            filename="voice.mp3", relative_path=f"{project_id}/media/voice.mp3", duration=4,
            sample_rate=44100, channels=2, codec="mp3", format_name="mp3",
        )
        transcript = {"language": "es", "segments": [{"text": "Keep"}]}
        record["audio"] = old_audio.model_dump()
        record["document"] = {"transcription": transcript, "phrase_transcription": {"version": 1, "phrases": []}}
        record["transcription_info"] = {"filename": "voice.json", "language": "es", "segment_count": 1, "word_count": 1}
        app.state.project_repository.save(project_id, record)
        with patch("app.api.v1.projects.probe_audio", new=AsyncMock(side_effect=media_module.InvalidMedia("not WAV"))):
            invalid = self.client.post(
                f"/api/v1/projects/{project_id}/audio?filename=voice.wav", content=b"invalid",
            )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(old_path.read_bytes(), b"old mp3")
        self.assertEqual(app.state.project_repository.get(project_id)["document"]["transcription"], transcript)

        wav_info = AudioInfo(
            filename="voice.wav", relative_path=f"{project_id}/media/voice.wav", duration=7,
            sample_rate=48000, channels=1, codec="pcm_s16le", format_name="wav",
        )
        with patch("app.api.v1.projects.probe_audio", new=AsyncMock(return_value=wav_info)):
            replaced = self.client.post(
                f"/api/v1/projects/{project_id}/audio?filename=voice.wav", content=b"new wav",
            )
        self.assertEqual(replaced.status_code, 201, replaced.text)
        saved = app.state.project_repository.get(project_id)
        self.assertEqual(saved["audio"]["filename"], "voice.wav")
        self.assertIsNone(saved["transcription_info"])
        self.assertNotIn("transcription", saved["document"])
        self.assertFalse(old_path.exists())
        self.assertEqual((self.project_root / project_id / "media" / "voice.wav").read_bytes(), b"new wav")

    def test_failed_persistence_rolls_back_same_filename_audio_and_transcription(self):
        project = self.create_project()
        project_id = project["id"]
        repo = app.state.project_repository
        audio_path = repo.media_path(project_id, "voice.wav")
        audio_path.write_bytes(b"old audio")
        transcript = {"language": "es", "segments": [{"text": "Antes"}]}
        record = repo.get(project_id)
        record["audio"] = AudioInfo(
            filename="voice.wav", relative_path=f"{project_id}/media/voice.wav", duration=3,
            sample_rate=48000, channels=2, codec="pcm_s16le", format_name="wav",
        ).model_dump()
        record["document"] = {"transcription": transcript, "design": {"fontSize": 64}}
        record["transcription_info"] = {"filename": "voice.json", "language": "es", "segment_count": 1, "word_count": 1}
        repo.save(project_id, record)
        replacement_info = AudioInfo(
            filename="voice.wav", relative_path=f"{project_id}/media/voice.wav", duration=9,
            sample_rate=44100, channels=1, codec="pcm_s16le", format_name="wav",
        )
        with patch("app.api.v1.projects.probe_audio", new=AsyncMock(return_value=replacement_info)), patch(
            "app.api.v1.projects._persist_snapshot", side_effect=OSError("simulated persistence failure")
        ):
            with self.assertRaises(OSError):
                self.client.post(
                    f"/api/v1/projects/{project_id}/audio?filename=voice.wav", content=b"new audio",
                )
        self.assertEqual(audio_path.read_bytes(), b"old audio")
        saved = repo.get(project_id)
        self.assertEqual(saved["audio"]["duration"], 3)
        self.assertEqual(saved["document"]["transcription"], transcript)
        self.assertIsNotNone(saved["transcription_info"])

    def test_delete_audio_removes_only_audio_and_invalidates_transcription(self):
        project = self.create_project()
        project_id = project["id"]
        repo = app.state.project_repository
        audio_path = repo.media_path(project_id, "voice.mp3")
        audio_path.write_bytes(b"mp3")
        video_path = repo.media_path(project_id, "video.mp4")
        video_path.write_bytes(b"video")
        record = repo.get(project_id)
        record["audio"] = AudioInfo(
            filename="voice.mp3", relative_path=f"{project_id}/media/voice.mp3", duration=1,
            sample_rate=44100, channels=2, codec="mp3", format_name="mp3",
        ).model_dump()
        record["media"] = MediaInfo(
            filename="video.mp4", relative_path=f"{project_id}/media/video.mp4", duration=1,
            width=10, height=10, fps=30, has_audio=True,
        ).model_dump()
        record["document"] = {
            "design": {"fontSize": 60}, "transcription": {"segments": []},
            "phrase_transcription": {"version": 1, "phrases": []},
        }
        record["transcription_info"] = {"filename": "voice.json", "language": "es", "segment_count": 0, "word_count": 0}
        repo.save(project_id, record)
        response = self.client.delete(f"/api/v1/projects/{project_id}/audio")
        self.assertEqual(response.status_code, 200, response.text)
        saved = self.client.get(f"/api/v1/projects/{project_id}").json()
        self.assertIsNone(saved["audio"])
        self.assertIsNone(saved["transcription_info"])
        self.assertNotIn("transcription", saved["document"])
        self.assertNotIn("phrase_transcription", saved["document"])
        self.assertEqual(saved["document"]["design"], {"fontSize": 60})
        self.assertFalse(audio_path.exists())
        self.assertTrue(video_path.exists())
        self.assertEqual(saved["media"]["filename"], "video.mp4")

    def test_audio_replace_and_delete_are_rejected_while_transcription_job_is_active(self):
        project = self.create_project()
        project_id = project["id"]
        active = app.state.active_transcription_projects
        active[project_id] = "active-job"
        try:
            replacement = self.client.post(
                f"/api/v1/projects/{project_id}/audio?filename=voice.mp3", content=b"mp3",
            )
            deletion = self.client.delete(f"/api/v1/projects/{project_id}/audio")
        finally:
            active.pop(project_id, None)
        self.assertEqual(replacement.status_code, 409)
        self.assertEqual(deletion.status_code, 409)
        self.assertIsNone(self.client.get(f"/api/v1/projects/{project_id}").json()["audio"])

    def test_audio_import_rejects_unsupported_extension_and_invalid_container(self):
        project = self.create_project()
        unsupported = self.client.post(
            f"/api/v1/projects/{project['id']}/audio?filename=voice.ogg", content=b"audio",
        )
        self.assertEqual(unsupported.status_code, 422)
        with patch("app.api.v1.projects.probe_audio", new=AsyncMock(side_effect=__import__(
            "app.projects.media", fromlist=["InvalidMedia"]
        ).InvalidMedia("extension mismatch"))):
            invalid = self.client.post(
                f"/api/v1/projects/{project['id']}/audio?filename=voice.wav", content=b"not wav",
            )
        self.assertEqual(invalid.status_code, 422)
        reopened = self.client.get(f"/api/v1/projects/{project['id']}").json()
        self.assertIsNone(reopened["audio"])
        self.assertFalse((self.project_root / project["id"] / "media" / "voice.wav").exists())

    def test_audio_stream_supports_ranges_for_wav_and_mp4_fallback(self):
        project = self.create_project()
        project_id = project["id"]
        repo = app.state.project_repository
        wav_path = repo.media_path(project_id, "voice.wav")
        wav_path.write_bytes(b"0123456789-wav")
        record = repo.get(project_id)
        record["audio"] = AudioInfo(
            filename="voice.wav", relative_path=f"{project_id}/media/voice.wav", duration=1,
            sample_rate=48000, channels=2, codec="pcm_s16le", format_name="wav",
        ).model_dump()
        repo.save(project_id, record)
        ranged = self.client.get(f"/api/v1/projects/{project_id}/audio", headers={"Range": "bytes=2-5"})
        self.assertEqual(ranged.status_code, 206)
        self.assertEqual(ranged.content, b"2345")
        self.assertEqual(ranged.headers["content-type"], "audio/wav")

        mp4_path = repo.media_path(project_id, "clip.mp4")
        mp4_path.write_bytes(b"mp4-audio-track")
        record = repo.get(project_id)
        record["audio"] = None
        record["media"] = MediaInfo(
            filename="clip.mp4", relative_path=f"{project_id}/media/clip.mp4", duration=1,
            width=10, height=10, fps=24, has_audio=True,
        ).model_dump()
        repo.save(project_id, record)
        fallback = self.client.get(f"/api/v1/projects/{project_id}/audio")
        self.assertEqual(fallback.status_code, 200)
        self.assertEqual(fallback.content, b"mp4-audio-track")
        self.assertEqual(fallback.headers["content-type"], "video/mp4")
        self.assertTrue(wav_path.exists())

    def test_invalid_media_extension_is_rejected_without_saving(self):
        project = self.create_project()
        response = self.client.post(
            f"/api/v1/projects/{project['id']}/media?filename=clip.mov",
            content=b"video",
        )
        self.assertEqual(response.status_code, 422)
        self.assertIsNone(self.client.get(f"/api/v1/projects/{project['id']}").json()["media"])

    def test_invalid_reimport_does_not_replace_previous_video(self):
        project = self.create_project()
        project_id = project["id"]
        info = MediaInfo(
            filename="sample.mp4", relative_path=f"{project_id}/media/sample.mp4",
            duration=1, width=10, height=10, fps=24, video_codec="h264", has_audio=False,
        )
        with patch("app.api.v1.projects.probe_video", new=AsyncMock(return_value=info)):
            first = self.client.post(
                f"/api/v1/projects/{project_id}/media?filename=sample.mp4", content=b"valid-video"
            )
        self.assertEqual(first.status_code, 201)
        with patch("app.api.v1.projects.probe_video", new=AsyncMock(side_effect=__import__(
            "app.projects.media", fromlist=["InvalidMedia"]
        ).InvalidMedia("corrupt MP4"))):
            rejected = self.client.post(
                f"/api/v1/projects/{project_id}/media?filename=sample.mp4", content=b"bad-video"
            )
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual((self.project_root / project_id / "media" / "sample.mp4").read_bytes(), b"valid-video")

    def test_import_transcription_preserves_engine_json_and_rejects_invalid_structure(self):
        project = self.create_project()
        path = Path(__file__).resolve().parents[2] / "output" / "burnei.json"
        transcript = json.loads(path.read_text(encoding="utf-8"))
        response = self.client.post(
            f"/api/v1/projects/{project['id']}/transcription?filename=burnei.json",
            content=path.read_bytes(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["language"], transcript["language"])
        self.assertEqual(response.json()["segment_count"], len(transcript["segments"]))
        restored = self.client.get(f"/api/v1/projects/{project['id']}").json()
        self.assertEqual(restored["transcription"], transcript)

        invalid = self.client.post(
            f"/api/v1/projects/{project['id']}/transcription?filename=bad.json",
            content=b'{"language":"es","segments":[{"start":2,"end":1,"words":[]}]}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(invalid.status_code, 422)

    def test_import_json_rejects_invalid_json_and_non_json_filename(self):
        project = self.create_project()
        invalid = self.client.post(
            f"/api/v1/projects/{project['id']}/transcription?filename=clip.json",
            content=b"not-json",
        )
        self.assertEqual(invalid.status_code, 422)
        bad_name = self.client.post(
            f"/api/v1/projects/{project['id']}/transcription?filename=clip.txt",
            content=b'{"segments": []}',
        )
        self.assertEqual(bad_name.status_code, 400)

    def test_missing_ffprobe_is_visible_but_keeps_imported_original(self):
        project = self.create_project()
        with patch("app.api.v1.projects.probe_video", new=AsyncMock(
            side_effect=FileNotFoundError("FFprobe missing")
        )):
            response = self.client.post(
                f"/api/v1/projects/{project['id']}/media?filename=sample.mp4",
                content=b"mp4-bytes",
            )
        self.assertEqual(response.status_code, 201)
        self.assertIn("FFprobe missing", response.json()["probe_error"])
        stored = self.project_root / project["id"] / "media" / "sample.mp4"
        self.assertEqual(stored.read_bytes(), b"mp4-bytes")


if __name__ == "__main__":
    unittest.main()
