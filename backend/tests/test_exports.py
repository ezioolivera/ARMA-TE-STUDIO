from dataclasses import replace
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.exports.adapters import ExportValidationError, export_ass_text, export_srt_text
from app.main import app
from app.projects.repository import ProjectRepository


def sample_project():
    return {
        "id": "project",
        "name": "Prueba exportación",
        "media": {"filename": "sample.mp4", "duration": 1.0, "width": 320, "height": 240, "fps": 24, "has_audio": True},
        "document": {
            "design": {
                "fontFamily": "Arial Bold", "fontSize": 56, "textColor": "#ffffff",
                "activeWordColor": "#37d7d5", "outlineColor": "#000000", "outlineWidth": 2,
                "shadow": True, "alignment": "center", "positionX": 50, "positionY": 62,
                "maxWidth": 85, "textFormat": "uppercase", "temporalEvents": [
                    {"id": "top", "start": .3, "end": .7, "position": "top", "positionX": 50, "positionY": 20},
                ],
            },
            "transcription": {
                "language": "es", "segments": [{
                    "id": "segment-1", "start": 0, "end": 1, "text": "Hola, país Ñandú",
                    "words": [
                        {"id": "word-1", "text": " Hola,", "start": 0, "end": .3},
                        {"id": "word-2", "text": " país", "start": .3, "end": .65},
                        {"id": "word-3", "text": " Ñandú", "start": .65, "end": 1},
                    ],
                }],
            },
        },
    }


class ExportAdapterTests(unittest.TestCase):
    def test_layout_width_validation_tolerates_only_subpixel_metric_differences(self):
        from app.exports.adapters import _ass_layout_validation_config, _ass_with_active_word_windows
        from designer import DesignerConfig, SubtitleCue, TimedWord, to_ass

        config = DesignerConfig.for_video(
            1080, 1920, max_width_ratio=.85, outline=0, shadow=0,
            watermark_box=None, vertical_position=.5,
            safe_top_ratio=0, safe_bottom_ratio=0,
        )
        word = TimedWord(" palabra", 0, 1)
        cue = SubtitleCue(0, 1, (word,), lines=((word,),))

        class FixedWidthMeasurer:
            width = 0.0

            def __init__(self, _font, _font_size, _bold):
                self.width = type(self).width

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def measure(self, _text, _font_size=None):
                return self.width

        def validate_width(width):
            FixedWidthMeasurer.width = width
            with patch("designer.layout.TextMeasurer", FixedWidthMeasurer):
                return _ass_with_active_word_windows([(cue, 0)], config)

        base_limit = 1080 * .85
        self.assertIn("Dialogue:", validate_width(base_limit))
        self.assertIn("Dialogue:", validate_width(base_limit + .2))
        with self.assertRaisesRegex(ValueError, "line 1"):
            validate_width(base_limit + .26)
        with self.assertRaisesRegex(ValueError, "line 1"):
            validate_width(base_limit + 20)
        self.assertEqual(config.max_width_ratio, .85)

        FixedWidthMeasurer.width = base_limit
        validation_config = _ass_layout_validation_config(config)
        with patch("designer.layout.TextMeasurer", FixedWidthMeasurer):
            self.assertEqual(to_ass((cue,), config), to_ass((cue,), validation_config))

    def test_srt_keeps_editable_segments_timestamps_and_spanish_format_without_mutation(self):
        project = sample_project()
        before = json.dumps(project, ensure_ascii=False)
        srt = export_srt_text(project)
        self.assertIn("00:00:00,000 --> 00:00:01,000", srt)
        self.assertIn("HOLA, PAÍS ÑANDÚ", srt)
        self.assertEqual(srt.count("\n1\n"), 0)
        self.assertEqual(json.dumps(project, ensure_ascii=False), before)

    def test_ass_maps_styles_karaoke_positions_and_splits_position_events(self):
        ass = export_ass_text(sample_project())
        self.assertIn("Style: Base,Arial", ass)
        self.assertIn("&H00FFFFFF", ass)
        self.assertIn("&H00D5D737", ass)
        self.assertIn("HOLA,", ass)
        self.assertIn(r"\kf30", ass)
        self.assertIn(r"\kf35", ass)
        self.assertIn(r"\pos(160,149)", ass)
        self.assertIn(r"\pos(160,48)", ass)
        self.assertIn("0:00:00.30", ass)
        self.assertIn("0:00:00.70", ass)

    def test_ass_keeps_selected_bundled_font_family_and_letter_spacing(self):
        project = sample_project()
        project["document"]["design"]["fontFamily"] = "Anton"
        project["document"]["design"]["letterSpacing"] = 2
        ass = export_ass_text(project)
        self.assertIn("Style: Base,Anton,", ass)
        self.assertIn(",100,100,0.592593,0,1,", ass)

    def test_ass_keeps_each_local_montserrat_weight_as_a_distinct_family(self):
        for family in ("Montserrat ExtraBold", "Montserrat Black"):
            with self.subTest(family=family):
                project = sample_project()
                project["document"]["design"]["fontFamily"] = family
                ass = export_ass_text(project)
                self.assertIn(f"Style: Base,{family},", ass)
                self.assertIn(f"Style: Karaoke,{family},", ass)
                base_style = next(line for line in ass.splitlines() if line.startswith("Style: Base,"))
                self.assertEqual(base_style.split(",")[7], "0")

    def test_ass_layout_manifest_matches_preview_word_windows_without_duplicate_karaoke(self):
        project = sample_project()
        project["document"]["design"]["temporalEvents"] = []
        manifest = [
            {"active_word_id": "word-1", "lines": [["word-1", "word-2"]], "font_size": 28},
            {"active_word_id": "word-2", "lines": [["word-1", "word-2"]], "font_size": 28},
            {"active_word_id": "word-3", "lines": [["word-2"], ["word-3"]], "font_size": 28},
        ]
        ass = export_ass_text(project, manifest)
        base = [line for line in ass.splitlines() if line.startswith("Dialogue: 0,")]
        karaoke = [line for line in ass.splitlines() if line.startswith("Dialogue: 1,")]
        self.assertEqual(len(base), 4)
        self.assertEqual(len(karaoke), 3)
        self.assertNotIn(r"\kf", "\n".join(karaoke))
        self.assertIn(r"\c&H00D5D737&", "\n".join(karaoke))
        self.assertIn("HOLA, PAÍS", base[0])
        self.assertIn("HOLA, PAÍS", base[1])
        self.assertIn("PAÍS", base[2])
        self.assertIn("ÑANDÚ", base[3])

    def test_ass_layout_scales_stroke_and_shadow_to_preview_dimensions(self):
        project = sample_project()
        project["document"]["design"]["temporalEvents"] = []
        manifest = [
            {"active_word_id": f"word-{index}", "lines": [[f"word-{index}"]], "font_size": 28, "render_scale": 2.5}
            for index in range(1, 4)
        ]
        ass = export_ass_text(project, manifest)
        style = next(line for line in ass.splitlines() if line.startswith("Style: Base,"))
        fields = style.split(",")
        self.assertEqual(fields[16], "5")
        self.assertEqual(fields[17], "5")

    def test_ass_local_font_uses_css_em_metrics_with_static_family(self):
        project = sample_project()
        project["document"]["design"]["fontFamily"] = "Anton"
        manifest = [
            {"active_word_id": f"word-{index}", "lines": [[f"word-{index}"]], "font_size": 28, "render_scale": 1}
            for index in range(1, 4)
        ]
        ass = export_ass_text(project, manifest)
        self.assertIn(r"{\fs48.5352}", ass)

    def test_local_font_ass_layout_size_uses_os2_metrics_for_one_and_two_line_cues(self):
        from app.exports.fonts import ass_font_size_scale

        for family in ("Anton", "Bebas Neue", "Montserrat ExtraBold", "Montserrat Black"):
            with self.subTest(family=family):
                project = sample_project()
                project["document"]["design"]["fontFamily"] = family
                project["document"]["design"]["temporalEvents"] = []
                manifest = [
                    {"active_word_id": "word-1", "lines": [["word-1", "word-2"], ["word-3"]], "font_size": 28, "render_scale": 1},
                    {"active_word_id": "word-2", "lines": [["word-1", "word-2"], ["word-3"]], "font_size": 28, "render_scale": 1},
                    {"active_word_id": "word-3", "lines": [["word-1", "word-2"], ["word-3"]], "font_size": 28, "render_scale": 1},
                ]
                ass = export_ass_text(project, manifest)
                scale = ass_font_size_scale(family)
                self.assertIn(fr"{{\fs{28 * scale:g}}}", ass)
                base_events = [line for line in ass.splitlines() if line.startswith("Dialogue: 0,")]
                self.assertEqual(len(base_events), 6)
                self.assertTrue(all(r"\pos(160," in line for line in base_events))

    def test_local_font_word_gaps_match_preview_without_changing_word_runs(self):
        from app.exports.fonts import ass_font_layout_metrics

        for family in ("Anton", "Bebas Neue", "Montserrat ExtraBold", "Montserrat Black"):
            for word_count in (2, 3, 4):
                with self.subTest(family=family, word_count=word_count):
                    project = sample_project()
                    design = project["document"]["design"]
                    design.update(fontFamily=family, wordSpacing=.28, letterSpacing=0, temporalEvents=[])
                    words = [
                        {"id": f"word-{i}", "text": f" {name}", "start": (i - 1) * .3, "end": i * .3}
                        for i, name in enumerate(("uno", "dos", "tres", "cuatro")[:word_count], start=1)
                    ]
                    project["document"]["transcription"]["segments"] = [{
                        "id": "segment-1", "start": 0, "end": word_count * .3,
                        "text": " ".join(word["text"].strip() for word in words), "words": words,
                    }]
                    lines = [[word["id"] for word in words]]
                    if word_count == 4:
                        lines = [["word-1", "word-2"], ["word-3", "word-4"]]
                    manifest = [
                        {"active_word_id": word["id"], "lines": lines, "font_size": 20, "render_scale": 1}
                        for word in words
                    ]
                    ass = export_ass_text(project, manifest)
                    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
                    base_events = [line for line in dialogues if line.startswith("Dialogue: 0,")]
                    karaoke_events = [line for line in dialogues if line.startswith("Dialogue: 1,")]
                    self.assertEqual(len(base_events), word_count * len(lines))
                    self.assertEqual(len(karaoke_events), word_count)
                    expected_base_spaces = [len(line) - 1 for _ in words for line in lines]
                    self.assertEqual(
                        [event.count(r"\fsp") // 2 for event in base_events],
                        expected_base_spaces,
                    )
                    for index, event in enumerate(karaoke_events):
                        active_line = next(line for line in lines if f"word-{index + 1}" in line)
                        self.assertEqual(event.count(r"\fsp"), (len(active_line) - 1) * 2)
                    self.assertIn(r"\c&H00D5D737&", "\n".join(karaoke_events))
                    self.assertNotIn(r"\kf", "\n".join(karaoke_events))
                    self.assertTrue(all(name.upper() in "\n".join(dialogues) for name in ("uno", "dos", "tres", "cuatro")[:word_count]))

                    space_ratio, _ = ass_font_layout_metrics(family)
                    # ASS fs uses the usWin scale for glyph height; the
                    # inter-word CSS gap stays in source-pixel coordinates.
                    expected_gap_adjustment = 20 * (.28 - space_ratio)
                    tag = re.search(r"\\fsp(-?\d+\.\d+)", base_events[0])
                    self.assertIsNotNone(tag)
                    self.assertAlmostEqual(float(tag.group(1)), expected_gap_adjustment, places=3)

    def test_local_font_baseline_alignment_is_metric_based_for_base_and_keyframes(self):
        from app.exports.fonts import ass_font_layout_metrics

        for family in ("Anton", "Bebas Neue", "Montserrat ExtraBold", "Montserrat Black"):
            with self.subTest(family=family):
                project = sample_project()
                project["document"]["design"].update(
                    fontFamily=family, wordSpacing=.28, positionY=50,
                    temporalEvents=[{"id": "top", "start": .3, "end": .65, "position": "top", "positionX": 50, "positionY": 20}],
                )
                manifest = [
                    {"active_word_id": "word-1", "lines": [["word-1"], ["word-2", "word-3"]], "font_size": 28, "render_scale": 1},
                    {"active_word_id": "word-2", "lines": [["word-1"], ["word-2", "word-3"]], "font_size": 28, "render_scale": 1},
                    {"active_word_id": "word-3", "lines": [["word-1"], ["word-2", "word-3"]], "font_size": 28, "render_scale": 1},
                ]
                ass = export_ass_text(project, manifest)
                _, baseline_ratio = ass_font_layout_metrics(family)
                shift = round(28 * baseline_ratio)
                position_values = [
                    tuple(map(int, match))
                    for match in re.findall(r"\\pos\((\d+),(\d+)\)", ass)
                ]
                # Each cue has two rows. Their centers use the CSS 1.08em pitch,
                # with only the measured font-family baseline delta added.
                self.assertTrue(position_values)
                self.assertTrue(all(x == 160 for x, _ in position_values))
                self.assertTrue(all((y - shift) in {105, 135, 33, 63} for _, y in position_values))

    def test_word_gap_override_restores_configured_letter_spacing(self):
        project = sample_project()
        project["document"]["design"].update(
            fontFamily="Anton", wordSpacing=.28, letterSpacing=2, temporalEvents=[],
        )
        manifest = [
            {"active_word_id": word_id, "lines": [["word-1", "word-2", "word-3"]], "font_size": 28, "render_scale": 2.666667}
            for word_id in ("word-1", "word-2", "word-3")
        ]
        ass = export_ass_text(project, manifest)
        style = next(line for line in ass.splitlines() if line.startswith("Style: Base,"))
        style_spacing = float(style.split(",")[13])
        self.assertAlmostEqual(style_spacing, 2 * 2.666667, places=5)
        base_event = next(line for line in ass.splitlines() if line.startswith("Dialogue: 0,"))
        spacing_values = [float(value) for value in re.findall(r"\\fsp(-?\d+\.\d+)", base_event)]
        self.assertEqual(len(spacing_values), 4)
        self.assertTrue(all(abs(value - style_spacing) < .0001 for value in spacing_values[1::2]))

    def test_invalid_transcript_is_reported_instead_of_writing_partial_output(self):
        project = sample_project()
        project["document"]["transcription"]["segments"][0]["words"][1]["end"] = .2
        with self.assertRaises(ExportValidationError):
            export_ass_text(project)


class ExportApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name) / "projects"
        settings = replace(__import__("app.main", fromlist=["settings"]).settings, project_data_dir=self.project_root)
        self.settings_patch = patch("app.main.settings", settings)
        self.settings_patch.start()
        self.context = TestClient(app)
        self.client = self.context.__enter__()
        self.project = self.client.post("/api/v1/projects", json={"name": "Export Test"}).json()
        response = self.client.patch(
            f"/api/v1/projects/{self.project['id']}",
            json={"document": sample_project()["document"]},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.settings_patch.stop()
        self.temp_dir.cleanup()

    def test_srt_and_ass_downloads_are_saved_inside_project_export_folder(self):
        for fmt, token in (("srt", "HOLA"), ("ass", "Dialogue:")):
            response = self.client.post(f"/api/v1/projects/{self.project['id']}/exports", json={"format": fmt})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(token, response.content.decode("utf-8"))
            self.assertIn("filename=", response.headers["content-disposition"])
            files = list((self.project_root / self.project["id"] / "exports").glob(f"*.{fmt}"))
            self.assertEqual(len(files), 1)
            download = self.client.get(f"/api/v1/projects/{self.project['id']}/exports/{files[0].name}")
            self.assertEqual(download.content, response.content)

    def test_export_endpoint_rejects_unknown_format_and_path_traversal_download(self):
        self.assertEqual(self.client.post(f"/api/v1/projects/{self.project['id']}/exports", json={"format": "exe"}).status_code, 422)
        self.assertEqual(self.client.get(f"/api/v1/projects/{self.project['id']}/exports/..%5Coutside.mp4").status_code, 400)

    def test_queued_video_job_can_be_cancelled_and_is_not_reported_running(self):
        job = self.client.app.state.job_manager.create("video_export")
        response = self.client.post(f"/api/v1/jobs/{job.job_id}/cancel")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "cancelled")
        self.assertEqual(self.client.get(f"/api/v1/jobs/{job.job_id}").json()["state"], "cancelled")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/FFprobe binaries are required")
    def test_mp4_job_renders_real_video_audio_resolution_fps_and_download(self):
        source = Path(self.temp_dir.name) / "source.mp4"
        generated = subprocess.run([
            shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:r=24:d=1",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source),
        ], capture_output=True, text=True, timeout=30)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        media_path = self.project_root / self.project["id"] / "media" / "sample.mp4"
        media_path.write_bytes(source.read_bytes())
        repository = ProjectRepository(self.project_root)
        project = repository.get(self.project["id"])
        project["media"] = {"filename": "sample.mp4", "relative_path": f"{self.project['id']}/media/sample.mp4", "duration": 1.0, "width": 320, "height": 240, "fps": 24, "video_codec": "h264", "audio_codec": "aac", "has_audio": True, "probe_error": None}
        project["document"]["design"]["fontFamily"] = "Anton"
        repository.save(self.project["id"], project)
        settings = replace(__import__("app.main", fromlist=["settings"]).settings, project_data_dir=self.project_root, ffmpeg_path=shutil.which("ffmpeg"))
        with patch("app.api.v1.exports.settings", settings):
            before = hashlib.sha256(media_path.read_bytes()).hexdigest()
            created = self.client.post(f"/api/v1/projects/{self.project['id']}/exports", json={"format": "mp4", "quality": "compact"})
            self.assertEqual(created.status_code, 202, created.text)
            job_id = created.json()["job_id"]
            deadline = time.monotonic() + 45
            state = None
            while time.monotonic() < deadline:
                state = self.client.get(f"/api/v1/jobs/{job_id}").json()
                if state["state"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(.1)
            self.assertEqual(state["state"], "succeeded", state)
            self.assertGreaterEqual(state["progress"], 99)
            self.assertTrue(state["download_url"])
            self.assertIn("Fuente verificada por libass: Anton", state["message"])
            filename = state["download_url"].split("/")[-1]
            download = self.client.get(f"/api/v1/projects/{self.project['id']}/exports/{filename}")
            self.assertEqual(download.status_code, 200)
            rendered = self.project_root / self.project["id"] / "exports" / filename
            self.assertGreater(rendered.stat().st_size, 1000)
            self.assertEqual(hashlib.sha256(media_path.read_bytes()).hexdigest(), before)
            probe = subprocess.run([shutil.which("ffprobe"), "-v", "error", "-show_entries", "stream=codec_name,width,height,avg_frame_rate", "-of", "json", str(rendered)], capture_output=True, text=True, timeout=15)
            streams = json.loads(probe.stdout)["streams"]
            video = next(stream for stream in streams if stream.get("codec_name") == "h264")
            audio = next(stream for stream in streams if stream.get("codec_name") == "aac")
            self.assertEqual((video["width"], video["height"], video["avg_frame_rate"]), (320, 240, "24/1"))
            self.assertEqual(audio["codec_name"], "aac")


if __name__ == "__main__":
    unittest.main()
