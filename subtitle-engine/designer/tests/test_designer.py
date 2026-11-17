import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "subtitle-engine"))

from designer import (  # noqa: E402
    DesignerConfig,
    SubtitleCue,
    SubtitleDocument,
    TimedWord,
    export_ass,
    export_srt,
    resolve_font,
    load_transcription,
    mark_highlights,
    segment_transcription,
    to_ass,
    to_srt,
)
from designer.layout import line_width_limit, validate_cues  # noqa: E402
from designer.metrics import TextMeasurer  # noqa: E402


def document(*words):
    return SubtitleDocument("es", 0.99, tuple(words))


class SegmentationTests(unittest.TestCase):
    def test_preserves_word_timestamps_and_groups_two_to_five(self):
        source = document(
            TimedWord(" Hola", 0.0, 0.3), TimedWord(" mundo", 0.3, 0.7),
            TimedWord(" hoy", 0.7, 1.0), TimedWord(" amigo", 1.0, 1.4),
            TimedWord(" querido", 1.4, 1.8), TimedWord(" siempre", 1.8, 2.1),
        )
        cues = segment_transcription(source, DesignerConfig(max_words=3))
        self.assertEqual([len(c.words) for c in cues], [3, 3])
        self.assertEqual(cues[0].start, 0.0)
        self.assertEqual(cues[0].end, 1.0)

    def test_pause_splits_even_when_group_is_short(self):
        source = document(TimedWord(" uno", 0.0, 0.2), TimedWord(" dos", 0.8, 1.0))
        cues = segment_transcription(source, DesignerConfig(pause_threshold=0.4))
        self.assertEqual(len(cues), 2)

    def test_duration_and_visual_length_split_groups(self):
        source = document(
            TimedWord(" palabra", 0.0, 0.7), TimedWord(" larguísima", 0.7, 1.4),
            TimedWord(" adicional", 1.4, 2.2),
        )
        cues = segment_transcription(source, DesignerConfig(max_chars=15, max_duration=1.5))
        self.assertGreater(len(cues), 1)

    def test_sentence_boundary_splits(self):
        source = document(TimedWord(" Hola.", 0.0, 0.4), TimedWord(" mundo", 0.4, 0.8))
        self.assertEqual(len(segment_transcription(source)), 2)

    def test_measured_wrap_is_balanced_and_within_85_percent(self):
        words = (
            TimedWord(" Hoy", 0.0, 0.2), TimedWord(" el", 0.2, 0.35),
            TimedWord(" camino", 0.35, 0.7), TimedWord(" parece", 0.7, 1.0),
            TimedWord(" interminable", 1.0, 1.7), TimedWord(" bajo", 1.7, 1.95),
            TimedWord(" la", 1.95, 2.05), TimedWord(" lluvia", 2.05, 2.4),
        )
        config = DesignerConfig(max_words=8, max_chars=80)
        cues = segment_transcription(document(*words), config)
        self.assertEqual(len(cues), 1)
        self.assertEqual(len(cues[0].display_lines), 2)
        font = resolve_font(config.font_name, config.bold, config.fallback_font_names)
        with TextMeasurer(font, config.font_size, config.bold) as metrics:
            widths = [metrics.measure("".join(w.text for w in line).strip()) for line in cues[0].display_lines]
        self.assertTrue(all(width <= line_width_limit(config) for width in widths))
        self.assertLess(abs(widths[0] - widths[1]), 220)

    def test_horizontal_resolution_is_supported(self):
        config = DesignerConfig.for_video(1920, 1080)
        cue = SubtitleCue(
            0.0, 1.0,
            (TimedWord(" Hola", 0.0, 0.4), TimedWord(" mundo", 0.4, 1.0)),
            lines=((TimedWord(" Hola", 0.0, 0.4), TimedWord(" mundo", 0.4, 1.0)),),
        )
        validate_cues((cue,), config)
        self.assertEqual(config.vertical_position, 0.55)

    def test_portrait_video_factory_matches_frame_dimensions(self):
        config = DesignerConfig.for_video(1080, 1920)
        self.assertEqual((config.play_res_x, config.play_res_y), (1080, 1920))
        self.assertEqual(config.vertical_position, 0.68)

    def test_watermark_overlap_is_rejected(self):
        config = DesignerConfig(vertical_position=0.8)
        cue = SubtitleCue(0.0, 1.0, (TimedWord(" Hola", 0.0, 1.0),))
        with self.assertRaisesRegex(ValueError, "watermark"):
            validate_cues((cue,), config)


class HighlightTests(unittest.TestCase):
    def test_highlight_matching_ignores_accents_and_punctuation(self):
        source = document(TimedWord(" país,", 0.0, 0.5), TimedWord(" otro", 0.5, 0.8))
        config = DesignerConfig(highlight_words=frozenset({"pais"}))
        marked = mark_highlights(source, config)
        self.assertTrue(marked.words[0].highlighted)
        self.assertFalse(marked.words[1].highlighted)


class ExportTests(unittest.TestCase):
    def setUp(self):
        words = (TimedWord(" Hola", 1.0, 1.4), TimedWord(" mundo.", 1.5, 2.0))
        self.cues = (SubtitleCue(1.0, 2.0, words),)

    def test_srt_has_standard_segment_timing(self):
        self.assertEqual(
            to_srt(self.cues),
            "1\n00:00:01,000 --> 00:00:02,000\nHola mundo.\n",
        )

    def test_ass_emits_white_base_and_word_specific_karaoke_layers(self):
        ass = to_ass(self.cues, DesignerConfig(fade_in_ms=0, fade_out_ms=0))
        self.assertIn("PlayResX: 1080\nPlayResY: 1920", ass)
        self.assertIn("Style: Base,Arial Narrow,58,&H00FFFFFF", ass)
        self.assertIn("Style: Karaoke,Arial Narrow,58,&H00D5D737", ass)
        self.assertIn("Dialogue: 1,0:00:01.00,0:00:01.40,Karaoke", ass)
        self.assertIn(r"{\alpha&H00&\kf40}Hola", ass)
        self.assertIn(r"{\alpha&H00&\kf50} mundo.", ass)
        self.assertIn(r"{\alpha&HFF&}", ass)
        self.assertIn(r"\pos(540,1306)", ass)

    def test_missing_font_uses_available_fallback(self):
        font = resolve_font("No Such Typeface", True, ("Arial",))
        self.assertEqual(font.family, "Arial")
        self.assertTrue(font.path.is_file())

    def test_bold_condensed_font_variant_is_selected(self):
        font = resolve_font("Arial Narrow", True, ("Arial",))
        self.assertEqual(font.family, "Arial Narrow")
        self.assertTrue(font.path.is_file())
        self.assertIn("nb", font.path.name.casefold())

    def test_exports_create_parent_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            ass = export_ass(self.cues, Path(temp) / "nested" / "x.ass")
            srt = export_srt(self.cues, Path(temp) / "other" / "x.srt")
            self.assertTrue(ass.is_file())
            self.assertTrue(srt.is_file())


class BurneiIntegrationTests(unittest.TestCase):
    def test_real_transcription_json_loads_segments_and_exports(self):
        source = ROOT / "output" / "burnei.json"
        if not source.exists():
            self.skipTest("output/burnei.json is not present")
        transcript = load_transcription(source)
        self.assertEqual(transcript.language, "es")
        self.assertGreater(len(transcript.words), 0)
        config = DesignerConfig()
        cues = segment_transcription(mark_highlights(transcript, config), config)
        validate_cues(cues, config)
        self.assertGreater(len(cues), 0)
        self.assertEqual(cues[0].start, transcript.words[0].start)
        self.assertIn(r"\kf", to_ass(cues, config))
        self.assertIn("--> ", to_srt(cues))
        self.assertTrue(all(len(cue.display_lines) <= 2 for cue in cues))
        word_events = [line for line in to_ass(cues, config).splitlines()
                       if line.startswith("Dialogue: 1,")]
        self.assertEqual(len(word_events), len(transcript.words))
        for line, word in zip(word_events, transcript.words):
            fields = line.split(",", 3)
            self.assertEqual(fields[1], f"0:{int(word.start // 60):02}:{word.start % 60:05.2f}")
            self.assertEqual(fields[2], f"0:{int(word.end // 60):02}:{word.end % 60:05.2f}")
        with tempfile.TemporaryDirectory() as temp:
            export_ass(cues, Path(temp) / "burnei.ass", config)
            export_srt(cues, Path(temp) / "burnei.srt")
            self.assertTrue((Path(temp) / "burnei.ass").stat().st_size > 0)
            self.assertTrue((Path(temp) / "burnei.srt").stat().st_size > 0)


if __name__ == "__main__":
    unittest.main()
