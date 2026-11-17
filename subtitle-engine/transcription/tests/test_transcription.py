import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from transcription import (  # noqa: E402
    TranscriptionConfig,
    TranscriptionEngine,
    TranscriptionResult,
    TranscriptSegment,
    TranscriptWord,
    export_json,
    export_srt,
    to_json,
    to_srt,
)


class ConfigTests(unittest.TestCase):
    def test_defaults_and_models(self):
        self.assertEqual(TranscriptionConfig().model_size, "small")
        for model in ("tiny", "base", "small", "medium"):
            self.assertEqual(TranscriptionConfig(model_size=model).model_size, model)

    def test_rejects_unsupported_model(self):
        with self.assertRaises(ValueError):
            TranscriptionConfig(model_size="large")


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.result = TranscriptionResult(
            language="es",
            language_probability=0.98,
            segments=(
                TranscriptSegment(
                    1.25,
                    2.5,
                    " Hola, mundo. ",
                    (TranscriptWord(" Hola,", 1.25, 1.7, 0.9),
                     TranscriptWord(" mundo.", 1.71, 2.5, 0.95)),
                ),
            ),
        )

    def test_srt_uses_segment_timing_and_standard_format(self):
        self.assertEqual(
            to_srt(self.result),
            "1\n00:00:01,250 --> 00:00:02,500\nHola, mundo.\n",
        )

    def test_json_contains_language_segments_words_and_timing(self):
        data = json.loads(to_json(self.result))
        self.assertEqual(data["language"], "es")
        self.assertEqual(data["segments"][0]["start"], 1.25)
        self.assertEqual(data["segments"][0]["words"][1]["end"], 2.5)

    def test_empty_srt(self):
        self.assertEqual(to_srt(TranscriptionResult(None, None, ())), "")

    def test_exporters_write_utf8_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            srt_path = export_srt(self.result, Path(temp_dir) / "nested" / "captions.srt")
            json_path = export_json(self.result, Path(temp_dir) / "transcript.json")
            self.assertIn("Hola, mundo.", srt_path.read_text(encoding="utf-8"))
            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(data["language"], "es")
            self.assertEqual(data["segments"][0]["words"][0]["text"], " Hola,")


class EngineTests(unittest.TestCase):
    def test_engine_maps_mocked_faster_whisper_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "audio.wav"
            audio.write_bytes(b"placeholder")
            segment = SimpleNamespace(
                start=0.0,
                end=1.2,
                text=" Hola",
                words=[SimpleNamespace(word=" Hola", start=0.0, end=1.2, probability=0.8)],
            )
            model = Mock()
            model.transcribe.return_value = (
                iter([segment]),
                SimpleNamespace(language="es", language_probability=0.99),
            )
            engine = TranscriptionEngine(TranscriptionConfig(model_size="base"))
            engine._model = model

            result = engine.transcribe(audio)

        self.assertEqual(result.language, "es")
        self.assertEqual(result.segments[0].words[0].start, 0.0)
        model.transcribe.assert_called_once_with(
            str(audio), language=None, beam_size=5,
            word_timestamps=True, vad_filter=True,
        )

    def test_missing_audio_fails_before_model_load(self):
        engine = TranscriptionEngine()
        with self.assertRaises(FileNotFoundError):
            engine.transcribe("missing-audio.wav")
        self.assertIsNone(engine._model)


if __name__ == "__main__":
    unittest.main()
