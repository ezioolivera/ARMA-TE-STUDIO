"""Faster-Whisper adapter. Model loading is deferred until transcription."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import TranscriptionConfig
from .models import TranscriptionResult, TranscriptSegment, TranscriptWord


class TranscriptionEngine:
    """Transcribe audio with word and segment timestamps using CPU int8."""

    def __init__(self, config: TranscriptionConfig | None = None) -> None:
        self.config = config or TranscriptionConfig()
        self._model: Any | None = None

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError(
                    "Faster-Whisper is missing. Install project requirements first."
                ) from exc
            self._model = WhisperModel(
                self.config.model_size,
                device=self.config.device,
                compute_type=self.config.compute_type,
            )
        return self._model

    def transcribe(self, audio_path: str | Path) -> TranscriptionResult:
        """Transcribe an audio file, auto-detecting its language by default."""
        path = Path(audio_path)
        if not path.is_file():
            raise FileNotFoundError(f"Audio file not found: {path}")

        segments_iter, info = self._get_model().transcribe(
            str(path),
            language=self.config.language,
            beam_size=self.config.beam_size,
            word_timestamps=True,
            vad_filter=True,
        )
        segments: list[TranscriptSegment] = []
        for segment in segments_iter:
            words = tuple(
                TranscriptWord(
                    text=word.word,
                    start=word.start,
                    end=word.end,
                    probability=getattr(word, "probability", None),
                )
                for word in (getattr(segment, "words", None) or ())
            )
            segments.append(
                TranscriptSegment(
                    start=float(segment.start),
                    end=float(segment.end),
                    text=segment.text,
                    words=words,
                )
            )

        return TranscriptionResult(
            language=getattr(info, "language", None),
            language_probability=getattr(info, "language_probability", None),
            segments=tuple(segments),
        )
