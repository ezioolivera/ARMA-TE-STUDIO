"""SRT and complete JSON exporters for transcription results."""

from __future__ import annotations

import json
from pathlib import Path

from .models import TranscriptionResult


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{whole_seconds:02},{millis:03}"


def to_srt(result: TranscriptionResult) -> str:
    """Format segment-level captions as standard SubRip text."""
    blocks = [
        f"{index}\n{_srt_timestamp(segment.start)} --> "
        f"{_srt_timestamp(segment.end)}\n{segment.text.strip()}"
        for index, segment in enumerate(result.segments, start=1)
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def to_json(result: TranscriptionResult, *, indent: int = 2) -> str:
    """Format full metadata, segments, and word-level timestamps as JSON."""
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=indent)


def export_srt(result: TranscriptionResult, destination: str | Path) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_srt(result), encoding="utf-8", newline="\n")
    return path


def export_json(
    result: TranscriptionResult, destination: str | Path, *, indent: int = 2
) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_json(result, indent=indent) + "\n", encoding="utf-8", newline="\n")
    return path
