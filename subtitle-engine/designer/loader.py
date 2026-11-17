"""Load and validate the JSON emitted by transcription/engine.py."""

import json
from pathlib import Path
from typing import Any

from .models import SubtitleDocument, TimedWord


def _finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError(f"{field_name} must be finite")
    return result


def load_transcription(source: str | Path) -> SubtitleDocument:
    """Read a transcription JSON path, preserving original word times."""
    path = Path(source)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise ValueError("Transcription JSON must contain a segments array")

    words: list[TimedWord] = []
    for segment_index, segment in enumerate(data["segments"]):
        if not isinstance(segment, dict):
            raise ValueError(f"Segment {segment_index} must be an object")
        segment_words = segment.get("words")
        if not isinstance(segment_words, list):
            raise ValueError(f"Segment {segment_index} must contain a words array")
        previous_start: float | None = None
        for word_index, item in enumerate(segment_words):
            label = f"segments[{segment_index}].words[{word_index}]"
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                raise ValueError(f"{label}.text must be a string")
            start = _finite_number(item.get("start"), f"{label}.start")
            end = _finite_number(item.get("end"), f"{label}.end")
            if start < 0 or end < start:
                raise ValueError(f"{label} has invalid timestamps")
            if previous_start is not None and start < previous_start:
                raise ValueError(f"Words in segment {segment_index} are not ordered")
            previous_start = start
            probability = item.get("probability")
            if probability is not None:
                probability = _finite_number(probability, f"{label}.probability")
            words.append(
                TimedWord(
                    text=item["text"], start=start, end=end,
                    probability=probability, source_segment=segment_index,
                )
            )

    language_probability = data.get("language_probability")
    if language_probability is not None:
        language_probability = _finite_number(language_probability, "language_probability")
    language = data.get("language")
    if language is not None and not isinstance(language, str):
        raise ValueError("language must be a string or null")
    return SubtitleDocument(language, language_probability, tuple(words))

