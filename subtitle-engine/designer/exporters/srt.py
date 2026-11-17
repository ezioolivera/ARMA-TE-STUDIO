"""Conventional SubRip export; styling and karaoke are intentionally omitted."""

from pathlib import Path

from ..models import SubtitleCue
from .io import write_text


def _timestamp(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    seconds_int, millis = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{seconds_int:02},{millis:03}"


def to_srt(cues: tuple[SubtitleCue, ...]) -> str:
    blocks = [
        f"{i}\n{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n{cue.text}"
        for i, cue in enumerate(cues, 1)
        if cue.text
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def export_srt(cues: tuple[SubtitleCue, ...], destination: str | Path) -> Path:
    return write_text(to_srt(cues), destination)

